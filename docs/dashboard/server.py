#!/usr/bin/env python3
"""
🧲 sticky dashboard backend — remote control for the reTerminal Sticky
through the tiny.technology device relay, behind a WebAuthn passkey gate.

Two device backends, one code path:
  • STICKY_MOCK=1: mock_device.MockSticky answers envelopes in-process per
    docs/API_CONTRACT.md and renders card specs to a real 800×480 4-gray PNG.
  • live (STICKY_MOCK=0 STICKY_DEVICE=sticky): tiny_relay.TinyRelay sends
    invoke envelopes with the owner's ~/.tiny/credentials.json Bearer token.

LIVE-MODE RULES (learned the hard way):
  • the firmware's `status` reply is an English sentence today and a JSON
    object tomorrow → parse_status() accepts BOTH and says which it saw.
  • the firmware implements exactly: render_ui, status, ask, voice,
    screenshot, ota. Everything else answers with a help string, so those
    commands are refused with 501 + a reason instead of spinning forever.
  • `screenshot` answers with a hosted plugin.tiny.technology/media/… URL in
    the reply TEXT (not images[]) → we extract it, allowlist it, and proxy
    the bytes at /api/screen.png so the mirror shows the REAL panel.
  • a click on the mirror in live mode cannot move a finger: it is logged as
    `sim_tap` ("simulated") and never as a real ui_tap. REAL taps are read
    owner-side from the platform event ring (GET /api/events → device_note
    "sticky: ui_tap button=N label=…") and shown as kind ui_tap.

Routes (all /api/* passkey-guarded except /api/health):
  GET  /api/health                     liveness + mode
  GET  /api/capabilities               which commands actually exist
  GET  /api/device                     device row / mock identity
  GET  /api/status                     normalized status (both reply shapes)
  GET  /api/status/latest              cached status + age_s (never blocks;
                                       background refresh ≤ every 120 s, only
                                       while a client polls — battery rule)
  POST /api/invoke {command,args,wait_s}  envelope round-trip
  GET  /api/result/{envelope_id}       redeem a pending reply (live)
  POST /api/screenshot                 take one; returns hosted + proxy URL
  GET  /api/screen.png                 latest frame (mock fb / proxied media)
  GET  /api/buttons                    touch targets of the last card sent
  POST /api/tap {button_id}            mock: real ui_tap · live: simulated
  GET  /api/activity?limit=N           envelopes + replies + REAL ui_taps
  /auth/*                              WebAuthn (see auth.py)

Run:  ./run.sh          (port 8787; STICKY_TLS=true for LAN passkeys)
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import time
from collections import deque
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import (FileResponse, HTMLResponse, JSONResponse,
                               StreamingResponse)
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool

import auth as sticky_auth
from tiny_relay import TinyRelay, RelayError, is_device_media_url

HERE = Path(__file__).resolve().parent
MOCK = os.getenv("STICKY_MOCK", "1").strip().lower() in ("1", "true", "yes")
DEVICE_SELECTOR = os.getenv("STICKY_DEVICE", "").strip()  # id or name (live mode)
PORT = int(os.getenv("STICKY_DASH_PORT", "8787"))


app = FastAPI(title="sticky dashboard", docs_url=None, redoc_url=None)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o for o in os.getenv("STICKY_CORS", "").split(",") if o] or ["https://sticky.cagatay.my"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── device backends ────────────────────────────────────────────────────
_mock = None
_relay: Optional[TinyRelay] = None
if MOCK:
    from mock_device import MockSticky
    _mock = MockSticky()
else:
    _relay = TinyRelay()

_activity: deque = deque(maxlen=200)

# ── persistence: the activity feed is the cockpit's AUDIT TRAIL and the OTA
# watch is a 30-min promise — both died with the process until 2026-08-26, and
# launchd (KeepAlive) respawns this server on every code deploy. An OTA staged
# before a respawn would lose its watch and end in silence, the exact failure
# the watch exists to prevent. JSONL append per event; state file for the
# watch; both .gitignore'd next to .sticky_auth.json.
_STATE_DIR = Path(__file__).resolve().parent
_ACTIVITY_LOG = _STATE_DIR / ".sticky_activity.jsonl"
_OTA_WATCH_FILE = _STATE_DIR / ".sticky_ota_watch.json"


def _activity_restore() -> None:
    try:
        lines = _ACTIVITY_LOG.read_text().splitlines()[-200:]
    except OSError:
        return
    restored = []
    for ln in lines:
        try:
            restored.append(json.loads(ln))
        except ValueError:
            continue  # a torn tail line (crash mid-write) is expected, not fatal
    for item in restored:  # file is oldest→newest; appendleft reverses to newest-first
        _activity.appendleft(item)


def _ota_watch_save(watch) -> None:
    """Persist EVERY device's watch ({device_id: {fw_before, ts}})."""
    try:
        slots = watch.all() if hasattr(watch, "all") else watch
        _OTA_WATCH_FILE.write_text(json.dumps(slots))
    except OSError:
        pass


def _ota_watch_restore(watch) -> None:
    """Load the keyed form; the pre-D1 flat form ({fw_before, ts}) belonged to
    STICKY_DEVICE — it is re-homed under that selector's slot."""
    try:
        w = json.loads(_OTA_WATCH_FILE.read_text())
    except (OSError, ValueError):
        return
    if not isinstance(w, dict):
        return
    if "fw_before" in w:
        if w.get("fw_before"):
            watch.slot(DEVICE_SELECTOR or "default").update(w)
        return
    for k, v in w.items():
        if isinstance(v, dict) and "fw_before" in v:
            watch.slot(k).update(v)


# envelopes the device hasn't answered yet — redeemed opportunistically on
# every /api/activity poll so a slow/sleeping device's reply still lands in
# the feed instead of evaporating (relay keeps results ~24 h; we give up at 15 min)
_pending_envelopes: Dict[str, Dict[str, Any]] = {}
_PENDING_TTL = 15 * 60
_PENDING_FILE = _STATE_DIR / ".sticky_pending.json"


def _pending_save() -> None:
    """Persist the registry — launchd respawns this server on every deploy, and
    an in-memory-only registry silently orphaned two envelopes queued behind an
    idle-sleeping glass (the chart cards that were left pending, the fixtures for this fix)."""
    try:
        _PENDING_FILE.write_text(json.dumps(_pending_envelopes))
    except OSError:
        pass


def _pending_restore() -> None:
    try:
        d = json.loads(_PENDING_FILE.read_text())
        if isinstance(d, dict):
            _pending_envelopes.update({k: v for k, v in d.items()
                                       if isinstance(v, dict) and "prompt" in v})
    except (OSError, ValueError):
        pass


_pending_restore()


def _log_activity(kind: str, **fields: Any) -> None:
    item = {"ts": time.time(), "kind": kind, **fields}
    _activity.appendleft(item)
    try:
        with _ACTIVITY_LOG.open("a") as f:
            f.write(json.dumps(item, default=str) + "\n")
    except OSError:
        pass  # a full disk must not take down the relay path


def _track_pending(envelope_id: Optional[str], prompt: str, args: Any = None) -> None:
    if envelope_id:
        _pending_envelopes[envelope_id] = {"prompt": prompt, "args": args, "ts": time.time(),
                                           "device": _dev_key()}
        _pending_save()


def _redeem_pending() -> None:
    """Poll every tracked unanswered envelope once; log replies into activity."""
    if MOCK or _relay is None or not _pending_envelopes:
        return
    now = time.time()
    for eid in list(_pending_envelopes):
        meta = _pending_envelopes[eid]
        tok = _current_selector.set(meta.get("device") or None)  # caches land on the right device
        try:
            _redeem_one(eid, meta, now)
        finally:
            _current_selector.reset(tok)


def _redeem_one(eid: str, meta: Dict[str, Any], now: float) -> None:
    assert _relay is not None
    try:
        payload = _relay.poll(eid)
    except RelayError:
        return  # transient — retry on the next activity poll
    if payload is None:
        if now - meta["ts"] > _PENDING_TTL:
            _pending_envelopes.pop(eid, None)
            _pending_save()
            _log_activity("expired", prompt=meta["prompt"], envelope_id=eid,
                          error=f"no reply after {_PENDING_TTL // 60} min — envelope abandoned")
        return
    _pending_envelopes.pop(eid, None)
    _pending_save()
    shot = _media_url_from_reply(payload)
    if shot:
        _last_shot.update({"url": shot, "ts": time.time(), "bytes": None})
    if str(meta["prompt"]).startswith("render_ui"):
        m = re.search(r"card_id=([\w.\-]+)", str(payload.get("result", "")))
        if m and meta.get("args") is not None:
            _remember_card(meta["args"], m.group(1))
    if str(meta["prompt"]).strip() == "status":
        # a late status reply is still the freshest truth we have — feed
        # the /api/status/latest cache and the OTA watch exactly like an
        # inline reply would (globals resolve at call time, so the
        # forward references to parse_status/_remember_status are fine)
        st = parse_status(payload.get("result"))
        _remember_status(st)
        fw = (st or {}).get("fw")
        if fw:
            _last_fw.update({"fw": fw, "ts": time.time()})
            _ota_watch_redeem(fw)
    _log_activity("reply", direction="in", prompt=meta["prompt"], late=True,
                  envelope_id=eid, result=str(payload.get("result", ""))[:500])


# ── multi-device (D1) ──────────────────────────────────────────────────
# The account's fleet holds THREE Stickies (relay rows: platform "esp32s3",
# names sticky / tiny-3096 / tiny-5f51; `capabilities` arrives as a JSON
# STRING, last_seen is epoch seconds, and the relay's own `online` flag flips
# false well under 90 s). Every per-device route takes ?device=<id|name> (or
# X-Sticky-Device; /api/invoke also accepts body.device). The selector lives in
# a request-scoped ContextVar — Starlette's threadpool and asyncio tasks copy
# the context — so the 22 legacy call sites keep their shape, and every
# per-device cache below is a PerDevice mapping keyed by the resolved device
# id. Default stays env STICKY_DEVICE (the launchd agent sets it).
from contextvars import ContextVar

_current_selector: ContextVar[Optional[str]] = ContextVar("sticky_device", default=None)
STICKY_PLATFORM_PREFIX = "esp32"
ONLINE_WINDOW_S = float(os.getenv("STICKY_ONLINE_WINDOW_S", "90"))
_FLEET_TTL_S = 15.0
_fleet_cache: Dict[str, Any] = {"rows": [], "ts": 0.0}


def _fleet(force: bool = False) -> List[Dict[str, Any]]:
    """The account's whole device list, cached 15 s (one relay round-trip
    serves the rail, the resolver and /api/devices together)."""
    assert _relay is not None
    if not force and _fleet_cache["rows"] and time.time() - _fleet_cache["ts"] < _FLEET_TTL_S:
        return _fleet_cache["rows"]
    rows = _relay.list_devices()
    _fleet_cache.update({"rows": rows, "ts": time.time()})
    return rows


def _is_sticky(row: Dict[str, Any]) -> bool:
    plat = str(row.get("platform") or "").lower()
    return plat.startswith(STICKY_PLATFORM_PREFIX) or str(row.get("name") or "").lower() == "sticky"


def _selector() -> str:
    return (_current_selector.get() or DEVICE_SELECTOR or "").strip()


_device_cache: Dict[str, Dict[str, Any]] = {}  # selector → {"row", "ts"}


def _resolve_device() -> Dict[str, Any]:
    """Live mode: the selector (?device= or STICKY_DEVICE) may be an id or a
    name — resolve via the fleet list. Exact name beats substring; newest
    last_seen wins a duplicate name (device-invoke.ts rule). Cached 60 s per
    selector: every invoke used to pay an extra fleet round-trip."""
    assert _relay is not None
    sel = _selector()
    if not sel:
        raise HTTPException(400, "live mode needs STICKY_DEVICE=<device id or name> or ?device=<id|name>")
    hit = _device_cache.get(sel)
    if hit and time.time() - hit["ts"] < 60:
        return hit["row"]
    devices = _fleet()
    row = next((d for d in devices if str(d.get("id")) == sel), None)
    if row is None:
        matches = [d for d in devices if sel.lower() == str(d.get("name", "")).lower()]
        if not matches:
            matches = [d for d in devices if sel.lower() in str(d.get("name", "")).lower()]
        if not matches:
            raise HTTPException(404, f"no device matching '{sel}' on the fleet")
        matches.sort(key=lambda d: d.get("last_seen") or 0, reverse=True)
        row = matches[0]
    _device_cache[sel] = {"row": row, "ts": time.time()}
    return row


def _resolve_device_id() -> str:
    return str(_resolve_device()["id"])


def _device_name() -> str:
    try:
        return str(_resolve_device().get("name") or "sticky")
    except Exception:
        return _selector() or "sticky"


def _dev_key() -> str:
    """Cache key of the CURRENT device: the resolved id when known, else the
    raw selector (so a device the relay cannot see right now still gets a
    stable slot instead of an exception inside a cache read)."""
    if MOCK:
        return "mock"
    sel = _selector()
    hit = _device_cache.get(sel)
    if hit:
        return str(hit["row"].get("id"))
    try:
        return str(_resolve_device().get("id"))
    except Exception:
        return sel or "default"


class PerDevice:
    """dict-shaped view onto per-device state: every read/write lands in the
    slot of the current device. Keeps `_last_shot.update(...)` /
    `_last_fw.get("fw")` call sites untouched; `.all()` exposes every slot for
    the rail and for persistence; `.slot(key)` addresses one device."""

    def __init__(self, factory, on_change=None):
        self._factory = factory
        self._by: Dict[str, Dict[str, Any]] = {}
        self._on_change = on_change

    def slot(self, key: Optional[str] = None) -> Dict[str, Any]:
        k = key or _dev_key()
        if k not in self._by:
            self._by[k] = self._factory()
        return self._by[k]

    def all(self) -> Dict[str, Dict[str, Any]]:
        return self._by

    def _changed(self) -> None:
        if self._on_change:
            self._on_change(self)

    def __getitem__(self, k):
        return self.slot()[k]

    def __setitem__(self, k, v):
        self.slot()[k] = v
        self._changed()

    def __contains__(self, k):
        return k in self.slot()

    def get(self, k, default=None):
        return self.slot().get(k, default)

    def update(self, *a, **kw):
        self.slot().update(*a, **kw)
        self._changed()


@app.middleware("http")
async def _device_scope(request: Request, call_next):
    sel = request.query_params.get("device") or request.headers.get("x-sticky-device")
    tok = _current_selector.set(sel.strip() if sel else None)
    try:
        return await call_next(request)
    finally:
        _current_selector.reset(tok)


# ── firmware capability truth table ────────────────────────────────────
# Verbs the running firmware ACTUALLY dispatches (tiny_node.cpp). Anything
# else replies with the help string — silently doing nothing on the panel is
# a lie on the screen, so the dashboard refuses those with a reason.
LIVE_VERBS = {"render_ui", "status", "sensors", "say", "ask", "voice",
              "screenshot", "miccheck", "page", "rotate", "tap", "scroll",
              "sleep", "ota", "messages"}
NOT_IN_FIRMWARE: Dict[str, str] = {
    "wake": "a sleeping device's radio is off — only the AI button (EXT1) or a sleep timer can wake it",
    "reprovision": "no `reprovision` verb — a heartbeat 401 (revoke the token) reopens the portal automatically",
    "image": "no `image` verb yet — send a card through render_ui (qr/kv/composite cover most needs)",
    "stream": "no `stream` verb yet",
}
VALID_COMMANDS = LIVE_VERBS | set(NOT_IN_FIRMWARE) | {"help"}

# verbs whose args flatten to positional text (firmware sscanf's the tail)
POSITIONAL_VERBS = {"ask", "voice", "ota", "page", "rotate", "miccheck", "sleep", "tap", "scroll"}  # legacy note: scalars now flatten positionally for every verb (dynamic passthrough)

# verbs that reply with a JSON object in `result` — parsed server-side so the
# UI can badge verdicts instead of regexing strings
_JSON_REPLY_VERBS = {"miccheck", "rotate", "tap", "scroll"}


def _attach_parsed(command: str, out: Dict[str, Any]) -> None:
    if command in _JSON_REPLY_VERBS and isinstance(out.get("result"), str):
        try:
            parsed = json.loads(out["result"])
            if isinstance(parsed, dict):
                out[command] = parsed
        except (ValueError, TypeError):
            pass


def _unavailable(command: str) -> Optional[str]:
    """Reason this command cannot work, or None. Mock implements everything."""
    if MOCK:
        return None
    return NOT_IN_FIRMWARE.get(command)


# ── status parsing (both reply shapes) ─────────────────────────────────
_SENTENCE_PATTERNS = {
    "fw": r"fw\s+([^\s,]+)",
    "heap_free": r"heap\s+(\d+)",
    "battery_pct": r"battery\s+(\d+)\s*%",
    "rssi_dbm": r"rssi\s+(-?\d+)",
    "uptime_s": r"up\s+(\d+)\s*s",
    "display": r"display\s+(\d+x\d+)",
}


def parse_status(result: Any) -> Dict[str, Any]:
    """Normalize a `status` reply. Accepts the JSON object the firmware is
    moving to AND the English sentence it answers with today:
      "sticky online: fw 0.8.1-m8, wifi up, heap 8300284, battery 99%,
       display 800x480 ready"
    Missing fields come back as None — the UI renders "—" instead of
    inventing a number."""
    raw = result
    obj: Optional[Dict[str, Any]] = None
    if isinstance(result, dict):
        obj = result
    elif isinstance(result, str):
        s = result.strip()
        if s.startswith("{"):
            try:
                parsed = json.loads(s)
                if isinstance(parsed, dict):
                    obj = parsed
            except ValueError:
                obj = None
    out: Dict[str, Any] = {k: None for k in
                           ("fw", "fw_commit", "battery_pct", "charging", "rssi_dbm", "heap_free",
                            "psram_free", "uptime_s", "sleeping", "display", "wifi",
                            "grammar_version",
                            # P0 heap instruments (fw 0.23.2): internal DRAM free,
                            # boot-lifetime low watermark, and last reset reason —
                            # the firmware's crash-forensics trio.
                            "internal_free", "min_free", "last_boot")}
    if obj is not None:
        for k in list(out):
            if k in obj:
                out[k] = obj[k]
        # tolerate alternative spellings the firmware might ship
        out["fw"] = out["fw"] or obj.get("version") or obj.get("firmware")
        out["battery_pct"] = out["battery_pct"] if out["battery_pct"] is not None else obj.get("battery")
        out["rssi_dbm"] = out["rssi_dbm"] if out["rssi_dbm"] is not None else obj.get("rssi")
        # fw 0.23.2 ships the low watermark as internal_min_free
        out["min_free"] = out["min_free"] if out["min_free"] is not None else obj.get("internal_min_free")
        out["shape"] = "json"
        out["raw"] = obj
        return out
    text = str(result or "")
    for key, pat in _SENTENCE_PATTERNS.items():
        m = re.search(pat, text, re.I)
        if m:
            val = m.group(1)
            out[key] = int(val) if key != "fw" and key != "display" else val
    if re.search(r"wifi\s+up", text, re.I):
        out["wifi"] = "up"
    elif re.search(r"wifi\s+down", text, re.I):
        out["wifi"] = "down"
    if re.search(r"charging", text, re.I):
        out["charging"] = True
    if re.search(r"\bsleep(ing)?\b", text, re.I):
        out["sleeping"] = True
    elif out["fw"]:
        out["sleeping"] = False  # it answered a relay poll, so it is awake
    out["shape"] = "sentence" if out["fw"] else "unparsed"
    out["raw"] = raw
    return out


# ── card geometry (must match firmware draw_buttons() exactly) ─────────
SCREEN_W, SCREEN_H = 800, 480
MARGIN, BUTTON_H, GAP, MAX_BUTTONS = 24, 56, 16, 4


def card_buttons(spec: Any) -> List[Dict[str, Any]]:
    """Derive touch targets from a card spec with the SAME geometry
    tiny_display.cpp draw_buttons() uses: bar at kH-kMargin-kButtonH, gap 16,
    n≤4 buttons splitting the usable width evenly. Accepts string buttons and
    the object form {"id","label"}, at the root or inside composite parts[]."""
    if not isinstance(spec, dict):
        return []
    btns = spec.get("buttons")
    if not isinstance(btns, list):
        for part in spec.get("parts") or []:
            if isinstance(part, dict) and isinstance(part.get("buttons"), list):
                btns = part["buttons"]
                break
    if not isinstance(btns, list) or not btns:
        return []
    btns = btns[:MAX_BUTTONS]
    n = len(btns)
    bar_y = SCREEN_H - MARGIN - BUTTON_H
    bw = (SCREEN_W - 2 * MARGIN - GAP * (n - 1)) // n
    out = []
    for i, b in enumerate(btns):
        if isinstance(b, dict):
            bid = str(b.get("id") or b.get("label") or f"b{i}")
            label = str(b.get("label") or bid)
        else:
            bid = label = str(b)
        x = MARGIN + i * (bw + GAP)
        out.append({"id": bid, "label": label, "index": i,
                    "bbox": [x, bar_y, x + bw, bar_y + BUTTON_H]})
    return out


# last card this dashboard pushed (live mode has no way to read the panel's
# current card back, so we only claim buttons for cards WE sent)
_last_card = PerDevice(lambda: {"spec": None, "card_id": None, "ts": 0.0, "buttons": []})
# last screenshot: hosted media URL + cached bytes for the guarded proxy
_last_shot = PerDevice(lambda: {"url": None, "ts": 0.0, "bytes": None})
# fw version from the most recent parsed status receipt — the OTA guard's
# "before" evidence (the manifest pointer itself is device-token-auth'd, so
# direction can only be proven by comparing receipts, not by reading intent)
_last_fw = PerDevice(lambda: {"fw": None, "ts": 0.0})
# most recent PARSED status + when it landed — the /api/status/latest cache.
# Filled by EVERY status round-trip (inline reply AND late redeem), so any
# probe from any surface refreshes it for free. A background refresh fires at
# most every _STATUS_REFRESH_MIN_S and ONLY while a client is actually polling
# /latest — the device is a 750 mAh pocket radio (POWER_BUDGET.md: every
# envelope is a fresh-TLS round-trip on its 5–60 s cadence), so an idle
# dashboard must cost it nothing and a closed tab costs literally zero.
_last_status = PerDevice(lambda: {"status": None, "ts": 0.0})
_STATUS_REFRESH_MIN_S = float(os.environ.get("STICKY_STATUS_REFRESH_S", "120"))
_status_refresh = PerDevice(lambda: {"last_attempt": 0.0, "in_flight": False})


def _remember_status(st: Optional[Dict[str, Any]]) -> None:
    if st:
        _last_status.update({"status": st, "ts": time.time()})


# an OTA staged by this dashboard, awaiting its fw_after receipt — the guard's
# second half. Redeemed (verdict logged) by the next status parse that shows a
# CHANGED version; expires unredeemed after 30 min (device may simply not have
# rebooted into the new slot yet — absence of evidence is reported as exactly that)
_ota_watch = PerDevice(lambda: {"fw_before": None, "ts": 0.0})  # survives launchd respawns
_ota_watch_restore(_ota_watch)
_activity_restore()  # must run after its definition — line-67 NameError crash-loop was casualty #10
_OTA_WATCH_TTL_S = 1800.0


def _fw_tuple(v: Any):
    """'0.14.7-m110' → (0,14,7) for ordering; None when unparseable."""
    m = re.match(r"^\s*(\d+)\.(\d+)(?:\.(\d+))?", str(v or ""))
    return (int(m.group(1)), int(m.group(2)), int(m.group(3) or 0)) if m else None


def _ota_watch_redeem(fw_now: str) -> None:
    """Called on every parsed status receipt. If an OTA is being watched and
    the version CHANGED, log the direction verdict and close the watch."""
    before = _ota_watch.get("fw_before")
    if not before or not fw_now:
        return
    if time.time() - _ota_watch["ts"] > _OTA_WATCH_TTL_S:
        _ota_watch.update({"fw_before": None, "ts": 0.0})
        _ota_watch_save(_ota_watch)
        _log_activity("ota_verdict", result=f"watch expired unredeemed — no status receipt showed a version change within 30 min of staging (fw_before={before})", verdict="expired")
        return
    if fw_now == before:
        return  # not rebooted into the new slot yet — keep watching
    a, b = _fw_tuple(before), _fw_tuple(fw_now)
    if a and b:
        verdict = "upgraded" if b > a else "DOWNGRADED" if b < a else "sidegraded"
    else:
        verdict = "changed (unorderable versions)"
    _log_activity("ota_verdict", result=f"{before} → {fw_now}", verdict=verdict)
    _ota_watch.update({"fw_before": None, "ts": 0.0})
    _ota_watch_save(_ota_watch)

_MEDIA_IN_TEXT = re.compile(r"https://[A-Za-z0-9.\-]+/media/[A-Za-z0-9._\-]+")


def _media_url_from_reply(reply: Dict[str, Any]) -> Optional[str]:
    """The screenshot verb puts the hosted URL in the reply TEXT
    ("screenshot: https://plugin.tiny.technology/media/….png"); a future
    firmware may use images[]. Accept both, allowlist either."""
    for im in reply.get("images") or []:
        if is_device_media_url(im.get("url")):
            return str(im["url"])
    m = _MEDIA_IN_TEXT.search(str(reply.get("result") or ""))
    if m and is_device_media_url(m.group(0)):
        return m.group(0)
    return None


# ── auth wiring (scout pattern: guard inline, ceremonies delegated) ────
def _guard(request: Request) -> Dict[str, Any]:
    return sticky_auth.require_auth(request)


def _cookie(resp: Response, token: str, request: Request) -> None:
    secure = request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https"
    resp.set_cookie("sticky_session", token, httponly=True, samesite="lax",
                    secure=secure, max_age=sticky_auth.TOKEN_TTL)


@app.get("/auth/status")
async def auth_status(request: Request):
    return sticky_auth.status(request)


@app.post("/auth/register/begin")
async def register_begin(request: Request):
    body = await request.json()
    # additional passkeys require an existing session; the FIRST enrollment seals
    if sticky_auth.has_credentials():
        _guard(request)
    return sticky_auth.begin_registration(request, label=body.get("label", "passkey"),
                                          bootstrap=body.get("bootstrap", ""))


@app.post("/auth/register/finish")
async def register_finish(request: Request):
    body = await request.json()
    out = sticky_auth.finish_registration(request, body["challenge_id"], body["credential"])
    resp = JSONResponse(out)
    _cookie(resp, out["token"], request)
    return resp


@app.post("/auth/login/begin")
async def login_begin(request: Request):
    return sticky_auth.begin_authentication(request)


@app.post("/auth/login/finish")
async def login_finish(request: Request):
    body = await request.json()
    out = sticky_auth.finish_authentication(request, body["challenge_id"], body["credential"])
    resp = JSONResponse(out)
    _cookie(resp, out["token"], request)
    return resp


@app.post("/auth/logout")
async def logout():
    resp = JSONResponse({"ok": True})
    resp.delete_cookie("sticky_session")
    return resp


@app.get("/auth/credentials")
async def creds_list(request: Request):
    _guard(request)
    return {"credentials": sticky_auth.list_credentials()}


@app.delete("/auth/credentials/{cred_id}")
async def creds_delete(cred_id: str, request: Request):
    _guard(request)
    return sticky_auth.delete_credential(cred_id)


# ── API ────────────────────────────────────────────────────────────────
@app.get("/api/health")
async def health():
    return {"ok": True, "mode": "mock" if MOCK else "live", "ts": time.time(),
            "device_selector": DEVICE_SELECTOR or None,
            "auth": {"enabled": sticky_auth.AUTH_ENABLED,
                     "setup_required": not sticky_auth.has_credentials()}}


@app.get("/api/capabilities")
async def capabilities(request: Request):
    _guard(request)
    # The device ADVERTISES its verbs in every heartbeat (fw tiny_node.cpp:
    # "advertise only verbs dispatch() actually implements") and the platform
    # stores them on the device record. Cross the static table with that live
    # list, so a verb shipped by OTA lights its button without a dashboard
    # deploy — the Sensors button stayed grey for a day because this table
    # was hardcoded. Device's word wins; static table is the offline fallback.
    advertised: List[str] = []
    fetched = False
    if not MOCK:
        try:
            row = await run_in_threadpool(_resolve_device)
            adv = (row or {}).get("capabilities")
            if isinstance(adv, str):  # platform may store JSON-serialized
                try:
                    adv = json.loads(adv)
                except ValueError:
                    adv = None
            if isinstance(adv, list):
                advertised = [str(v) for v in adv]
                fetched = True
        except RelayError:
            pass  # platform unreachable — fall back to the static table
    known = sorted(VALID_COMMANDS | set(advertised))
    cmds = []
    for c in known:
        if c == "help" or MOCK:
            cmds.append({"command": c, "available": _unavailable(c) is None,
                         "reason": _unavailable(c), "source": "static"})
        elif c in advertised:
            cmds.append({"command": c, "available": True, "reason": None,
                         "source": "device",
                         "new": c not in VALID_COMMANDS or None})
        elif fetched and c in LIVE_VERBS:
            cmds.append({"command": c, "available": False, "source": "device",
                         "reason": "not advertised by the firmware's heartbeat — "
                                   "the running build may predate this verb or dropped it"})
        else:
            cmds.append({"command": c, "available": _unavailable(c) is None,
                         "reason": _unavailable(c), "source": "static"})
    return {"ok": True, "mode": "mock" if MOCK else "live", "commands": cmds,
            "advertised": fetched,
            "taps": "real" if MOCK else "simulated",
            "note": None if MOCK else
            "live mode: mirror clicks are simulated (the glass cannot be touched from here); "
            "real taps arrive from the device's own event stream"}


@app.get("/api/devices")
async def devices_list(request: Request):
    """The account's Stickies (D1): id, name, online (last_seen < 90 s), last
    seen, and whatever this dashboard already knows per device — fw, battery,
    rotation from the last parsed status, the last screenshot's age. Never a
    device round-trip: one cached fleet list."""
    _guard(request)
    now = time.time()
    if MOCK:
        st = parse_status(_mock.dispatch("status")["result"])
        return {"ok": True, "mode": "mock", "default": "mock-sticky", "online_window_s": ONLINE_WINDOW_S,
                "devices": [{"id": "mock-sticky", "name": "sticky (software mock)", "platform": "mock",
                             "online": True, "last_seen": int(now), "age_s": 0, "fw": st.get("fw"),
                             "battery_pct": st.get("battery_pct"), "rotation": st.get("rotation"),
                             "status_age_s": 0, "shot_age_s": None, "default": True}]}
    try:
        rows = await run_in_threadpool(_fleet)
    except RelayError as e:
        raise HTTPException(e.status or 424, str(e))
    default_id = None
    if DEVICE_SELECTOR:
        try:
            tok = _current_selector.set(None)
            try:
                default_id = await run_in_threadpool(_resolve_device_id)
            finally:
                _current_selector.reset(tok)
        except HTTPException:
            pass
    out = []
    for r in rows:
        if not _is_sticky(r):
            continue
        did = str(r.get("id"))
        seen = r.get("last_seen")
        age = round(now - float(seen), 1) if isinstance(seen, (int, float)) else None
        st = (_last_status.all().get(did) or {})
        status = st.get("status") or {}
        shot = _last_shot.all().get(did) or {}
        out.append({
            "id": did, "name": r.get("name"), "platform": r.get("platform"), "kind": r.get("kind"),
            "online": age is not None and age < ONLINE_WINDOW_S,
            "relay_online": r.get("online"),
            "last_seen": seen, "age_s": age,
            "fw": (_last_fw.all().get(did) or {}).get("fw") or status.get("fw"),
            "battery_pct": status.get("battery_pct"), "charging": status.get("charging"),
            "rotation": status.get("rotation"),
            "status_age_s": round(now - st["ts"], 1) if st.get("ts") else None,
            "shot_age_s": round(now - shot["ts"], 1) if shot.get("ts") and shot.get("url") else None,
            "default": did == default_id,
        })
    out.sort(key=lambda d: (not d["default"], str(d["name"]).lower()))
    return {"ok": True, "mode": "live", "default": default_id, "online_window_s": ONLINE_WINDOW_S,
            "devices": out}


@app.get("/api/device")
async def device_info(request: Request):
    _guard(request)
    if MOCK:
        st = parse_status(_mock.dispatch("status")["result"])
        return {"ok": True, "mode": "mock", "device": {
            "id": "mock-sticky", "name": "sticky (software mock)",
            "kind": "reterminal-sticky", "online": True, **st}}
    try:
        row = await run_in_threadpool(_resolve_device)
        return {"ok": True, "mode": "live", "device": row}
    except RelayError as e:
        raise HTTPException(e.status or 424, str(e))


_FOLD_BUDGET = 1024  # draw_wrapped's static fold buffer — cuts SILENTLY past this (fw bug, docs/cards.md)
_FOLD_GROWERS = {"\u2713": 4, "\u2717": 3, "\u00bd": 3, "\u00bc": 3, "\u00be": 3,
                 "\u2026": 3, "\u00df": 2, "\u00e6": 2, "\u00c6": 2, "\u0153": 2, "\u0152": 2}


def _folded_len(text: str) -> int:
    """Approximate the firmware ascii_fold output length: ASCII 1:1, known
    multi-char expansions counted, every other codepoint folds to one byte."""
    return sum(1 if ord(ch) < 0x80 else _FOLD_GROWERS.get(ch, 1) for ch in text)


def _render_budget_warnings(spec: Any, path: str = "") -> List[str]:
    """Per-field fold-budget check mirroring draw_wrapped's per-call buffer:
    body, each list item, each composite part. Warn — never refuse: the glass
    still renders the first 1024 bytes fine."""
    warns: List[str] = []
    if not isinstance(spec, dict):
        return warns
    body = spec.get("body")
    if isinstance(body, str) and (n := _folded_len(body)) > _FOLD_BUDGET:
        warns.append(f"{path}body ≈{n}B after fold — the glass cuts it silently at {_FOLD_BUDGET} (fw bug, filed)")
    for i, it in enumerate(spec.get("items") or []):
        if isinstance(it, str) and (n := _folded_len(it)) > _FOLD_BUDGET:
            warns.append(f"{path}items[{i}] ≈{n}B after fold — silently cut at {_FOLD_BUDGET}")
    for i, part in enumerate(spec.get("parts") or []):
        warns.extend(_render_budget_warnings(part, f"{path}parts[{i}]."))
    return warns


def _do_invoke(command: str, args: Any, wait_s: float) -> Dict[str, Any]:
    """Blocking envelope round-trip (runs in a threadpool so a 60 s pending
    invoke does not freeze the whole dashboard)."""
    prompt = command if args in (None, {}, "") else f"{command} {json.dumps(args)}"
    if isinstance(args, dict):
        # every non-render_ui verb takes POSITIONAL text, not JSON — the
        # firmware sscanf/strncmp's the tail: `page home`, `rotate 90`,
        # `miccheck 3`, `sleep 60`, `tap 400 240`, `ask <text>`, `voice 6`.
        positional: Any = None
        if command in ("ask", "voice", "ota", "say"):
            positional = args.get("text") or args.get("channel") or args.get("seconds") or args.get("s")
        elif command == "page":
            positional = args.get("target") or args.get("page") or args.get("name")
        elif command == "rotate":
            positional = args.get("deg", args.get("degrees", args.get("mode")))
        elif command in ("miccheck", "sleep"):
            positional = args.get("seconds", args.get("s"))
        elif command == "tap":
            if args.get("x") is not None and args.get("y") is not None:
                positional = f"{int(args['x'])} {int(args['y'])}"
        if positional is not None and positional != "":
            prompt = f"{command} {positional}"
    elif isinstance(args, (str, int, float)):
        # scalars flatten positionally for EVERY verb — the firmware
        # sscanf's tails, and JSON-quoting a bare string (`play "abc"`)
        # helps nobody; dynamic heartbeat-advertised verbs ride this too
        prompt = f"{command} {args}"
    _log_activity("envelope", direction="out", prompt=prompt)

    budget_warns = _render_budget_warnings(args) if command == "render_ui" else []

    if MOCK:
        reply = _mock.dispatch(prompt)
        if budget_warns:
            reply["render_warning"] = budget_warns
        for im in reply.get("images", []):
            if im.get("url") == "mock://screen":
                im["url"] = f"/api/screen.png?ts={int(time.time())}"
        if command == "render_ui":
            _remember_card(args, reply.get("card_id"))
        if command == "screenshot":
            _last_shot.update({"url": None, "ts": time.time(), "bytes": None})
        _log_activity("reply", direction="in", prompt=prompt,
                      result=str(reply.get("result", ""))[:500], images=reply.get("images"))
        return {"ok": True, "mode": "mock", "envelope_id": f"env_mock_{int(time.time()*1000)}", **reply}

    out = _relay.invoke(_resolve_device_id(), prompt, wait_s=wait_s)
    if budget_warns:
        out["render_warning"] = budget_warns
    # media allowlist — same rule as use_device
    images = [im for im in out.get("images", []) if is_device_media_url(im.get("url"))]
    if images != out.get("images", []):
        out["images_refused"] = len(out.get("images", [])) - len(images)
    out["images"] = images
    shot = _media_url_from_reply(out)
    if shot:
        out["screenshot_url"] = shot
        _last_shot.update({"url": shot, "ts": time.time(), "bytes": None})
    if command == "render_ui" and not out.get("pending"):
        # firmware echoes the card_id the panel actually COMMITTED — keep that
        # truth, not just "the last card we sent" (mirror honesty rule)
        m = re.search(r"card_id=([\w.\-]+)", str(out.get("result", "")))
        if m:
            out["committed_card_id"] = m.group(1)
        _remember_card(args, m.group(1) if m else None)
    if command == "status":
        out["status"] = parse_status(out.get("result"))
        _remember_status(out["status"])
        fw = (out["status"] or {}).get("fw")
        if fw:
            _last_fw.update({"fw": fw, "ts": time.time()})
            _ota_watch_redeem(fw)
    _attach_parsed(command, out)
    _log_activity("reply" if not out.get("pending") else "pending", direction="in",
                  prompt=prompt, result=str(out.get("result", ""))[:500],
                  envelope_id=out.get("envelope_id"), images=images or None)
    if out.get("pending"):
        _track_pending(out.get("envelope_id"), prompt, args)
    return {"ok": True, "mode": "live", **out}


def _remember_card(spec: Any, card_id: Optional[str]) -> None:
    if not isinstance(spec, dict):
        return
    _last_card.update({"spec": spec, "card_id": card_id or spec.get("card_id"),
                       "ts": time.time(), "buttons": card_buttons(spec)})


@app.post("/api/invoke")
async def invoke(request: Request):
    _guard(request)
    body = await request.json()
    if body.get("device") and not request.query_params.get("device"):
        _current_selector.set(str(body["device"]).strip())
    command = str(body.get("command", "")).strip()
    args = body.get("args")
    wait_s = min(float(body.get("wait_s", 45)), 120.0)
    if command not in VALID_COMMANDS:
        # device's word wins (same rule as /api/capabilities): a verb the
        # firmware advertises in its heartbeat is callable the moment the
        # OTA lands — `play` shipped mid-morning and the dashboard could
        # SEE it but not SEND it until this passthrough. Unknown-to-both
        # stays a hard 400.
        adv = await run_in_threadpool(_advertised_verbs)
        if command not in adv:
            raise HTTPException(400, f"unknown command '{command}' — static {sorted(VALID_COMMANDS)}, device-advertised {sorted(set(adv) - VALID_COMMANDS)}")
    reason = _unavailable(command) if command in VALID_COMMANDS else None
    _painting = command in glass_etiquette.GLASS_VERBS and not (
        command == "play" and isinstance(args, str) and args.strip().startswith("status"))
    if _painting and not body.get("force"):
        w = glass_etiquette.open_window()
        if w:
            _log_activity("glass_busy", prompt=command, error=w["text"][:160])
            raise HTTPException(409, f"glass busy — another client announced an open "
                                     f"window: \"{w['text'][:160]}\". Pass force:true "
                                     f"to paint over it anyway (logged).")
    if reason:
        _log_activity("unavailable", prompt=command, error=reason)
        raise HTTPException(501, reason)
    try:
        if command == "ota" and not MOCK:
            # OTA direction-blindness guard (API_CONTRACT §OTA): the fw trigger
            # fires on strcmp DIFFERENCE, not "newer", and the pointer is
            # device-token-auth'd so we cannot read intent — the only honest
            # guard is receipts. Capture fw_before with a fresh status probe,
            # stage the OTA, and hand both to the operator + activity feed.
            pre = await run_in_threadpool(_do_invoke, "status", None, 20.0)
            fw_before = (pre.get("status") or {}).get("fw") or _last_fw.get("fw")
            out = await run_in_threadpool(_do_invoke, command, args, wait_s)
            guard = {
                "fw_before": fw_before,
                "note": "fw OTA trigger is direction-blind (fires on difference, not newer) — "
                        "when the device returns, run status and compare: fw_after should be "
                        "NEWER than fw_before; if it is older the pointer was stale and this "
                        "was a silent rollback",
            }
            out["ota_guard"] = guard
            if fw_before:
                _ota_watch.update({"fw_before": fw_before, "ts": time.time()})
                _ota_watch_save(_ota_watch)
            _log_activity("ota_guard", prompt="ota staged", result=f"fw_before={fw_before or 'unknown'} — verify fw_after via status when the device returns")
            return out
        return await run_in_threadpool(_do_invoke, command, args, wait_s)
    except RelayError as e:
        _log_activity("error", prompt=command, error=str(e))
        raise HTTPException(e.status or 424, str(e))


def _json_salvage(raw: str):
    """Best-effort parse of a TRUNCATED JSON object (fw 0.14.13 cuts the
    sensors reply at ~767 B mid-string — reported to the firmware side). Scans
    once tracking string/escape state and the container stack, then closes
    whatever is open. Returns (obj, truncated) — obj None when hopeless."""
    try:
        return json.loads(raw), False
    except ValueError:
        pass
    stack, in_str, esc = [], False, False
    for ch in raw:
        if esc:
            esc = False
            continue
        if in_str:
            if ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch in "{[":
            stack.append("}" if ch == "{" else "]")
        elif ch in "}]" and stack:
            stack.pop()
    candidate = raw + ('"' if in_str else "") + "".join(reversed(stack))
    try:
        return json.loads(candidate), True
    except ValueError:
        # last resort: drop back to the final comma and re-close
        idx = raw.rfind(",")
        if idx > 0:
            obj, _ = _json_salvage(raw[:idx])
            if obj is not None:
                return obj, True
        return None, True


@app.get("/api/sensors")
async def sensors_card(request: Request):
    """Live SHT40/IMU/RTC/gauge read — `sensors` replies JSON since 0.9.7-m9.
    Passed through verbatim (field names are the firmware's own); the frontend
    owns the provenance caveats (gyro deliberately off, mAh gated on mah_trusted)."""
    _guard(request)
    try:
        out = await run_in_threadpool(_do_invoke, "sensors", None, 60.0)
    except RelayError as e:
        raise HTTPException(e.status or 424, str(e))
    if out.get("pending"):
        return {"ok": True, "pending": True, "envelope_id": out.get("envelope_id"),
                "mode": out.get("mode")}
    raw = out.get("result")
    obj, truncated = None, False
    if isinstance(raw, dict):
        obj = raw
    elif isinstance(raw, str):
        obj, truncated = _json_salvage(raw)
    return {"ok": True, "mode": out.get("mode"), "sensors": obj,
            "truncated": truncated,
            "result": None if obj is not None else raw,
            "envelope_id": out.get("envelope_id")}


@app.get("/api/status")
async def status_card(request: Request):
    """Normalized device status — the one route the StatusCard needs. Parses
    the JSON object form and the English sentence form."""
    _guard(request)
    try:
        out = await run_in_threadpool(_do_invoke, "status", None, 60.0)
    except RelayError as e:
        raise HTTPException(e.status or 424, str(e))
    if out.get("pending"):
        return {"ok": True, "pending": True, "envelope_id": out.get("envelope_id"),
                "mode": out.get("mode")}
    st = out.get("status") or parse_status(out.get("result"))
    return {"ok": True, "mode": out.get("mode"), "status": st,
            "result": out.get("result"), "envelope_id": out.get("envelope_id")}


@app.get("/api/status/latest")
async def status_latest(request: Request):
    """Cached status + its age — NEVER a blocking device round-trip, so it can
    be polled every 15 s by every open tab for free. A hit here may kick ONE
    background `status` envelope, but at most every _STATUS_REFRESH_MIN_S and
    only because a client is actually polling (this endpoint IS the
    client-connected signal): POWER_BUDGET.md §cadence — every envelope costs
    the 750 mAh pocket device a fresh-TLS round-trip, so no client = no probes."""
    _guard(request)
    if MOCK:
        st = parse_status(_mock.dispatch("status")["result"])
        _remember_status(st)
        return {"ok": True, "mode": "mock", "status": st, "age_s": 0.0,
                "refreshing": False, "refresh_min_s": _STATUS_REFRESH_MIN_S}
    # redeem any late replies first (a pending status envelope may have landed)
    if _pending_envelopes:
        await run_in_threadpool(_redeem_pending)
    now = time.time()
    age = None if not _last_status["ts"] else round(now - _last_status["ts"], 1)
    stale = age is None or age > _STATUS_REFRESH_MIN_S
    if stale and not _status_refresh["in_flight"] \
            and now - _status_refresh["last_attempt"] >= _STATUS_REFRESH_MIN_S:
        _status_refresh.update({"last_attempt": now, "in_flight": True})

        async def _bg_refresh() -> None:
            try:
                await run_in_threadpool(_do_invoke, "status", None, 60.0)
            except Exception:
                pass  # cache keeps its honest age; the window gates the retry
            finally:
                _status_refresh["in_flight"] = False

        asyncio.get_running_loop().create_task(_bg_refresh())
    return {"ok": True, "mode": "live", "status": _last_status["status"],
            "age_s": age, "refreshing": _status_refresh["in_flight"],
            "refresh_min_s": _STATUS_REFRESH_MIN_S}


@app.get("/api/result/{envelope_id}")
async def result(envelope_id: str, request: Request):
    _guard(request)
    if MOCK:
        return {"ok": True, "result": "mock mode answers synchronously — nothing pending"}
    try:
        payload = await run_in_threadpool(_relay.poll, envelope_id)
    except RelayError as e:
        raise HTTPException(e.status or 424, str(e))
    if payload is None:
        return {"ok": True, "pending": True, "envelope_id": envelope_id}
    _pending_envelopes.pop(envelope_id, None)  # redeemed here — stop auto-polling it
    _pending_save()
    shot = _media_url_from_reply(payload)
    if shot:
        payload["screenshot_url"] = shot
        _last_shot.update({"url": shot, "ts": time.time(), "bytes": None})
    _log_activity("reply", direction="in", envelope_id=envelope_id,
                  result=str(payload.get("result", ""))[:500])
    return {"ok": True, "envelope_id": envelope_id, **payload}


@app.post("/api/screenshot")
async def screenshot(request: Request):
    """Take a fresh frame. Live: `screenshot` → hosted media URL (the device
    uploads a real 800×480 PNG of its framebuffer) → cached for the proxy."""
    _guard(request)
    try:
        out = await run_in_threadpool(_do_invoke, "screenshot", None, 90.0)
    except RelayError as e:
        raise HTTPException(e.status or 424, str(e))
    if out.get("pending"):
        return {"ok": True, "pending": True, "envelope_id": out.get("envelope_id"),
                "detail": "device has not answered yet — it polls the relay every 5 s"}
    url = out.get("screenshot_url")
    if not MOCK and not url:
        # do not pretend: say exactly what the device replied
        raise HTTPException(502, f"screenshot verb did not return a hosted media URL — "
                                 f"device replied: {str(out.get('result'))[:300]!r}")
    ts = int(time.time())
    # Contract: orientation travels with the pixels.
    # Firmware ask #2 adds "rotation" to the screenshot reply; until it ships
    # we accept a text-form fallback and otherwise say "assumed 0" honestly.
    rotation, rot_src = None, "absent (assume 0 — pre-ask-#2 firmware)"
    if isinstance(out.get("rotation"), (int, float)) and int(out["rotation"]) in (0, 90, 180, 270):
        rotation, rot_src = int(out["rotation"]), "reply"
    else:
        rm = re.search(r"rotation[=: ]+(0|90|180|270)\b", str(out.get("result") or ""))
        if rm:
            rotation, rot_src = int(rm.group(1)), "reply-text"
    return {"ok": True, "mode": out.get("mode"), "url": url,
            "proxy_url": f"/api/screen.png?ts={ts}", "ts": ts,
            "rotation": rotation, "rotation_source": rot_src,
            "envelope_id": out.get("envelope_id"), "result": out.get("result")}


def _fetch_shot() -> bytes:
    url = _last_shot.get("url")
    if not url:
        raise HTTPException(404, "no frame yet — POST /api/screenshot first")
    if _last_shot.get("bytes"):
        return _last_shot["bytes"]
    if not is_device_media_url(url):
        raise HTTPException(502, "refusing a non-allowlisted media URL")
    try:
        r = requests.get(url, timeout=25, headers={"User-Agent": "sticky-dashboard/1.0"})
    except requests.RequestException as e:
        raise HTTPException(502, f"media fetch failed: {e}")
    if r.status_code != 200 or not r.content:
        raise HTTPException(502, f"media fetch HTTP {r.status_code}")
    _last_shot["bytes"] = r.content
    return r.content


@app.get("/api/screen.png")
async def screen_png(request: Request):
    _guard(request)  # cookie or ?token= (auth.py checks query fallback)
    if MOCK:
        return Response(content=_mock.screen_png(), media_type="image/png",
                        headers={"Cache-Control": "no-store"})
    data = await run_in_threadpool(_fetch_shot)
    return Response(content=data, media_type="image/png",
                    headers={"Cache-Control": "no-store"})


@app.get("/api/buttons")
async def buttons(request: Request):
    _guard(request)
    if MOCK:
        return {"buttons": _mock.buttons(), "card_id": _mock.card_id, "taps": "real",
                "source": "mock renderer bounding boxes"}
    # Live: the relay gives us no way to read the panel's current card back, so
    # we only claim buttons for the last card THIS dashboard sent, using the
    # firmware's own geometry.
    if not _last_card["buttons"]:
        return {"buttons": [], "taps": "simulated", "card_id": None,
                "note": "no card with buttons has been sent from this dashboard yet — "
                        "the panel may still show buttons from a card sent elsewhere, "
                        "and those coordinates are not knowable from here"}
    return {"buttons": _last_card["buttons"], "card_id": _last_card["card_id"],
            "taps": "simulated", "card_ts": _last_card["ts"],
            "source": "geometry of the last render_ui card sent from this dashboard "
                      "(800x480, margin 24, 56px button bar, gap 16)"}


@app.post("/api/tap")
async def tap(request: Request):
    _guard(request)
    body = await request.json()
    bid = str(body.get("button_id", ""))
    if MOCK:
        ev = _mock.tap(bid)
        _log_activity("ui_tap", source="mock", **{k: v for k, v in ev.items() if k != "kind"})
        return {"ok": True, **ev}
    btn = next((b for b in _last_card["buttons"] if b["id"] == bid), None)
    if btn is None:
        raise HTTPException(404, f"button '{bid}' is not on the last card sent from here")
    _log_activity("sim_tap", button_id=bid, label=btn["label"], simulated=True,
                  card_id=_last_card["card_id"],
                  note="simulated tap from the dashboard — the device did NOT feel this")
    return {"ok": True, "simulated": True, "button_id": bid, "label": btn["label"],
            "bbox": btn["bbox"],
            "note": "simulated: a click here cannot press the glass. Only a finger on the "
                    "panel produces a real ui_tap (it shows up in this feed as kind=ui_tap)."}


# ── real device events (owner-side read of the platform event ring) ────
# Physical taps reach the platform as kind=device_note with detail
# "sticky: ui_tap button=N label=…". GET /api/events (owner Bearer) is the
# only owner-side route that surfaces them — verified 2026-08-25.
_events: Dict[str, Any] = {"since": 0, "seen": set(), "last_pull": 0.0, "primed": False,
                           "error": None}
_UI_TAP = re.compile(r"ui_tap\s+button=(-?\d+)(?:\s+label=(.*))?$")


def _pull_device_events() -> None:
    if MOCK or _relay is None:
        return
    now = time.time()
    if now - _events["last_pull"] < 4.0:
        return
    _events["last_pull"] = now
    name = _device_name()
    prefix = f"{name}:"
    try:
        if not _events["primed"]:
            # walk to the tail once, keep only the last few so the feed starts
            # with real history instead of the whole 200-event ring
            since, tail = 0, []
            for _ in range(8):
                batch = _relay.events(since)
                if not batch:
                    break
                tail = batch
                since = batch[-1].get("id") or since
                if len(batch) < 50:
                    break
            _events["since"] = since
            _events["primed"] = True
            fresh = [e for e in tail if str(e.get("detail", "")).startswith(prefix)][-8:]
        else:
            batch = _relay.events(_events["since"])
            if batch:
                _events["since"] = batch[-1].get("id") or _events["since"]
            fresh = [e for e in batch if str(e.get("detail", "")).startswith(prefix)]
        _events["error"] = None
    except Exception as e:  # never let the feed take the page down
        _events["error"] = str(e)
        return
    if len(_events["seen"]) > 2000:
        _events["seen"] = set(list(_events["seen"])[-500:])
    for e in fresh:
        eid = e.get("id")
        if eid in _events["seen"]:
            continue
        _events["seen"].add(eid)
        detail = str(e.get("detail", ""))[len(prefix):].strip()
        ts = float(e.get("created") or time.time())
        m = _UI_TAP.search(detail)
        if m:
            _activity.appendleft({"ts": ts, "kind": "ui_tap", "source": "device",
                                  "event_id": eid, "button_id": m.group(1),
                                  "label": (m.group(2) or "").strip() or None,
                                  "card_id": None})
        else:
            _activity.appendleft({"ts": ts, "kind": e.get("kind") or "device_event",
                                  "source": "device", "event_id": eid, "result": detail})


# ── DM rail (gate 7) — the same tiny.technology messages the glass's inbox
# shows, surfaced on the dashboard. Semantics inherited from TinyRelay:
#   inbox  = poll-safe
#   thread = MARKS EVERY INBOUND MESSAGE READ → only served on explicit open
#   send   = viaTiny declares who wrote it; dashboard default is human (False)
def _need_relay() -> TinyRelay:
    if _relay is None:
        raise HTTPException(503, "DM rail needs live mode (STICKY_MOCK=0) — mock has no tiny.technology account")
    return _relay


@app.get("/api/messages")
async def dm_inbox(request: Request, limit: int = 20):
    _guard(request)
    _need_relay()
    threads = await run_in_threadpool(_relay.messages, limit)
    return {"threads": threads}


@app.get("/api/messages/{peer}")
async def dm_thread(request: Request, peer: str, limit: int = 30):
    _guard(request)
    _need_relay()
    # side effect (upstream): opening a thread marks its inbound messages read
    out = await run_in_threadpool(_relay.thread, peer, limit)
    _log_activity("dm_open", peer=peer, prompt=f"dm thread @{peer}",
                  result=f"{len(out.get('messages') or [])} messages (inbound marked read)")
    return out


@app.post("/api/messages")
async def dm_send(request: Request):
    _guard(request)
    body = await request.json()
    to = str(body.get("to") or "").strip()
    message = body.get("message") or ""
    via_tiny = bool(body.get("via_tiny", False))
    if not to:
        raise HTTPException(400, "missing 'to'")
    _need_relay()
    try:
        out = await run_in_threadpool(_relay.send_dm, to, message, via_tiny)
    except RelayError as e:
        raise HTTPException(400, str(e))
    _log_activity("dm_send", peer=to, prompt=f"dm → @{out.get('to', {}).get('login', to)}",
                  result=f"delivered {out.get('delivered')}", via_tiny=via_tiny)
    return out


@app.get("/api/activity")
async def activity(request: Request, limit: int = 50):
    _guard(request)
    if not MOCK:
        await run_in_threadpool(_pull_device_events)
        await run_in_threadpool(_redeem_pending)
    items = sorted(_activity, key=lambda it: it.get("ts") or 0, reverse=True)
    out: Dict[str, Any] = {"activity": items[: max(1, min(limit, 200))]}
    if _pending_envelopes:
        out["in_flight"] = [
            {"envelope_id": eid, "prompt": m["prompt"], "age_s": int(time.time() - m["ts"])}
            for eid, m in list(_pending_envelopes.items())
        ]
    if _events.get("error"):
        out["events_error"] = _events["error"]
    return out


@app.on_event("startup")
async def _reaper_task() -> None:
    """Background redeemer: /api/activity redeems opportunistically, but with no
    browser open (owner out, pocket glass asleep) a late reply would sit in the
    relay until the 24 h GC. Poll gently: 20 s cadence only while envelopes are
    in flight, single-flight via threadpool, TTL expiry handled in _redeem_pending."""
    async def loop() -> None:
        while True:
            try:
                if _pending_envelopes and not MOCK:
                    await run_in_threadpool(_redeem_pending)
            except Exception:
                pass  # the reaper must never die; next tick retries
            await asyncio.sleep(20)
    asyncio.create_task(loop())


# ── image → e-ink frames ───────────────────────────────────────────────
import dither as sticky_dither
from fastapi import UploadFile, File


@app.post("/api/dither")
async def api_dither(request: Request, file: UploadFile = File(...)):
    """Upload any image → exact-size raw e-ink frames + previews.

    Frames are served at /frames/<sha16>/<orient>_<depth>.bin — capability
    URLs (unguessable content hash), fetchable WITHOUT a session cookie
    because the DEVICE has no passkey. The firmware's media host-allowlist
    only trusts plugin.tiny.technology/media/* today; until the firmware
    extends it (or the media flow proxies these), the device cannot fetch
    them. Nothing secret leaks: the
    frames are the user's own image, dithered.
    """
    _guard(request)
    data = await file.read()
    if not data:
        raise HTTPException(400, "empty upload")
    if len(data) > 20 * 1024 * 1024:
        raise HTTPException(413, "image too large (20MB cap)")
    try:
        manifest = await run_in_threadpool(sticky_dither.dither_image, data,
                                           file.filename or "upload")
    except Exception as e:  # PIL raises many types for a non-image
        raise HTTPException(422, f"not a decodable image: {e}")
    _log_activity("dither", sha=manifest["sha"], filename=manifest["filename"])
    return manifest


@app.get("/api/dither")
async def api_dither_contract():
    """The contract for POST /api/dither — machine-readable, PUBLIC on purpose.

    Two consumers (the firmware image path, iOS image-send) depend on the frame
    format and neither holds a passkey, so this is unguarded like /api/health: it
    describes a format, it exposes no user image and no device state. Before
    this route existed, GET /api/dither fell through to the SPA catch-all and
    answered 200 text/html — a client reading that would have "successfully"
    parsed a web page as a spec. Every number below is read from dither.py at
    call time, so this can never drift from the packer that produces bytes.
    """
    d = sticky_dither
    return {
        "endpoint": "POST /api/dither",
        "auth": "session cookie required for POST (this GET is public)",
        "request": {
            "content_type": "multipart/form-data",
            "field": "file",
            "accepts": ["image/png", "image/jpeg", "image/gif", "image/bmp",
                        "image/webp", "image/tiff"],
            "accepts_note": "anything Pillow can decode; the extension is not "
                            "trusted, the bytes are sniffed. Alpha (RGBA/LA/PA) "
                            "is flattened onto WHITE before dithering.",
            "max_bytes": 20 * 1024 * 1024,
            "errors": {"400": "empty upload", "413": "over max_bytes",
                       "422": "not a decodable image"},
        },
        "response": "manifest JSON: {sha, filename, source, frames{...}, ts, format_note}",
        "outputs": {
            "landscape_1bit": {"bytes": d.BYTES_1BIT, "panel_px": list(d.LANDSCAPE)},
            "landscape_4gray": {"bytes": d.BYTES_4GRAY, "panel_px": list(d.LANDSCAPE)},
            "portrait_1bit": {"bytes": d.BYTES_1BIT, "panel_px": list(d.LANDSCAPE)},
            "portrait_4gray": {"bytes": d.BYTES_4GRAY, "panel_px": list(d.LANDSCAPE)},
        },
        "packing": {
            "1bit": "row-major, MSB-first, 8 px/byte, bit 1 = WHITE",
            "4gray": "row-major, MSB-first, 4 px/byte, 2 bits/px, "
                     "0b00=BLACK 0b01=dark 0b10=light 0b11=WHITE",
            "gray_levels": list(d.GRAY_LEVELS),
            "gray_levels_note": "2bpp index 0..3 → 8-bit luminance; evenly spaced, "
                                "NOT measured off the panel. If the SSD1677 LUT's "
                                "middle greys land elsewhere, the levels move here "
                                "and nothing else changes.",
            "confirmed_against_firmware": {
                "status": "CONFIRMED — this is no longer an assumption",
                "how": "read out of the firmware's own Canvas, not out of a datasheet "
                       "or a guess, on 2026-08-26 at fw 0.16.5-m14",
                "evidence": {
                    "4gray_4px_per_byte": "canvas.cpp:12  stride_((width + 3U) / 4U) "
                                          "→ 200 B/row × 480 = 96000 B exactly",
                    "4gray_msb_first": "canvas.cpp:105  shift = (3 - (x & 0x03)) * 2 "
                                       "→ pixel x%4==0 occupies bits 7:6",
                    "4gray_interleaved_not_planes": "canvas.cpp:104-108 writes 2 bits "
                                                    "in place in ONE buffer; there is no "
                                                    "second plane anywhere in the driver",
                    "gray4_polarity": "canvas.h:7-10  Black=0 DarkGray=1 LightGray=2 "
                                      "White=3 — matches 0b00=BLACK..0b11=WHITE",
                    "mono_polarity_and_order": "sticky_display.cpp "
                                               "convert_gray4_to_monochrome_in_place: "
                                               "gray >= 2 → bit set at (7 - bit) "
                                               "→ bit 1 = WHITE, MSB-first; the driver "
                                               "also declares "
                                               "SEEED_EPAPER_PIXEL_FORMAT_MONO1_MSB",
                },
                "still_a_choice_not_a_fact": "portrait = rotate 90° CW is a COMPOSITION "
                                             "choice of this endpoint, independent of "
                                             "packing. Nothing in the firmware forces it; "
                                             "say the word and it flips.",
            },
            "do_not_pre_rotate_180": {
                "warning": "whoever writes the image-push verb: feed these bytes to the "
                           "CANVAS, not to the panel. sticky_display_refresh() calls "
                           "rotate_framebuffer_180() AND reverse_pixel_order() per byte "
                           "on its way to the glass — that is the panel-mounting "
                           "compensation. Pre-rotating here would double-apply it and "
                           "land the picture upside down, which is exactly the class of "
                           "bug that cost this project the IMU rotation night.",
            },
            "firmware_mono_threshold": "if a mono refresh is used on a gray4 buffer the "
                                       "firmware thresholds at gray >= 2 (levels 2,3 → "
                                       "white). This endpoint's 1-bit output does NOT go "
                                       "that route — it is an independent Floyd-Steinberg "
                                       "dither from the greyscale source, which keeps "
                                       "detail a 2-level threshold throws away. Use the "
                                       "1bit .bin for mono refresh, not a thresholded "
                                       "gray4 one.",
        },
        "aspect": {
            "policy": "LETTERBOX — never crop",
            "detail": "fit inside the canvas keeping aspect (LANCZOS), centre it, "
                      "pad the remainder with WHITE.",
            "why": "cropping silently destroys the part of the picture the user "
                   "chose to send, and on e-ink white is 'no ink' — white bars cost "
                   "no power, look like bezel, and leave no ghosting, which black "
                   "bars would. A crop mode can be added as an explicit opt-in "
                   "parameter; it will never be the default.",
            "portrait": "the panel has ONE native raster (800x480). 'portrait' composes "
                        "on 480x800 then rotates 90° CW into that raster — same byte "
                        "count, a composition choice, not a second buffer size. CW is "
                        "assumed; report it if fw rotates the other way.",
        },
        "frames_fetch": {
            "url": "/frames/<sha16>/<orient>_<depth>.bin  (+ .png preview)",
            "auth": "none — capability URL, the content hash IS the secret",
            "cache": "immutable, max-age=31536000",
        },
        "on_glass": {
            "reachable_by_device": False,
            "reason": "there is NO relay verb that pushes an image to the panel yet "
                      "(grammar v4 card types: text/list/kv/composite/qr/keyboard/menu; "
                      "image is designed, not shipped). Separately, the firmware media "
                      "allowlist trusts only plugin.tiny.technology/media/*, so the "
                      "device could not fetch /frames/* even if a verb existed.",
            "unblocks": "these bytes are byte-exact for the panel NOW, so the firmware "
                        "and iOS clients can build against them; the first on-glass pixel "
                        "waits on grammar v5.",
            "verified": "server-side only (curl + byte-count assertions). No on-glass "
                        "claim is made by this endpoint.",
        },
    }


@app.get("/api/gallery")
async def api_gallery(request: Request):
    _guard(request)
    return {"items": await run_in_threadpool(sticky_dither.list_gallery)}


_FRAME_NAME = re.compile(r"^(landscape|portrait)_(1bit|4gray)\.(bin|png)$|^thumb\.png$|^manifest\.json$")


@app.get("/frames/{sha}/{name}")
async def frame_file(sha: str, name: str):
    # capability URL: sha16 of content is the secret; strict name whitelist
    if not re.fullmatch(r"[0-9a-f]{16}", sha) or not _FRAME_NAME.fullmatch(name):
        raise HTTPException(404, "no such frame")
    target = sticky_dither.FRAMES_DIR / sha / name
    if not target.is_file():
        raise HTTPException(404, "no such frame")
    media = ("application/octet-stream" if name.endswith(".bin")
             else "application/json" if name.endswith(".json") else "image/png")
    return FileResponse(target, media_type=media,
                        headers={"Cache-Control": "public, max-age=31536000, immutable"})


# ── /api/frames — the remote render rail (anim grammar → frames) ───────
# Source (1): anim-grammar JSON (EINK_ANIMATION.md
# §4) → Pillow rasterize → dither.py packers → manifest + exact-size raw
# frames shaped for tiny_stream_play(manifest_url, interval_s, max_frames).
import frames as sticky_frames
import glass_etiquette


@app.post("/api/frames")
async def api_frames_create(request: Request):
    """anim-grammar JSON in → rendered frame sequence out (manifest).

    Caps enforced HERE (server promises, device re-enforces): 120 frames /
    60s per sequence, refusals carry the arithmetic. WebAuthn session
    required — this route spends CPU and disk on demand.
    """
    _guard(request)
    try:
        spec = await request.json()
    except Exception:
        raise HTTPException(400, "body must be anim-grammar JSON")
    try:
        manifest = await run_in_threadpool(sticky_frames.render_sequence, spec)
    except sticky_frames.SpecError as e:
        raise HTTPException(422, str(e))
    _log_activity("frames", seq_id=manifest["seq_id"], anim_id=manifest["anim_id"],
                  count=manifest["count"], format=manifest["format"])
    return manifest


@app.get("/api/frames")
async def api_frames_contract():
    """Machine-readable contract — PUBLIC on purpose (same lesson as GET
    /api/dither: without this route the SPA catch-all serves index.html
    and a firmware/agent author parses a web page as a spec)."""
    f = sticky_frames
    return {
        "endpoint": "POST /api/frames",
        "auth": "WebAuthn session (cookie) — GETs below are capability-URL public",
        "request": "anim-grammar JSON, EINK_ANIMATION.md §4: {anim_id, version:1, "
                   "mode:1bit_partial|4gray_partial|full, frame_budget_ms>=250, "
                   "loop:-1|n, frames:[{op:blit|rect|ellipse|scene|card_diff|delay|flush,"
                   "...}], patches:{id:{region,bitmap_b64|fill}}} — ellipse = solid "
                   "ellipse in its tile; scene = {clear?, shapes:[{kind:rect|ellipse,"
                   "tile,color}]} composes many primitives into ONE frame",
        "semantics": "canvas persists across ops; each paint op emits one frame; "
                     "delay holds the last frame in interval quanta; flush is "
                     "device-owned (no-op here); loop>1 materializes, -1 sets "
                     "closed_loop for device replay",
        "caps": {"max_frames": f.MAX_FRAMES, "max_duration_s": f.MAX_DURATION_S,
                 "min_interval_ms": f.MIN_INTERVAL_MS},
        "manifest": "GET /api/frames/<seq_id>/manifest → {frames:[urls], "
                    "interval_ms, format:'1bit'|'gray4', frame_bytes, count, "
                    "closed_loop} — matches tiny_stream_play(manifest_url, "
                    "interval_s, max_frames)",
        "frames": "GET /api/frames/<seq_id>/<n>.raw → exact "
                  f"{f.BYTES_1BIT} B (1bit) / {f.BYTES_4GRAY} B (gray4), "
                  "canvas-space per dither.py's confirmed format; <n>.png = preview",
        "ansi_twin": "GET /ansi/<seq_id> (and GET /duck, the built-in demo) → "
                     "chunked text/plain: the same frames as 256-gray half-block "
                     "ANSI at manifest cadence, looped to the 60s cap — public, "
                     "read-only. curl sticky.cagatay.my/duck",
    }


@app.post("/api/frames/prompt")
async def api_frames_prompt(request: Request):
    """Source (2): "show me a spinning duck" → agent-authored
    anim-grammar spec → the SAME validator/renderer/store as hand-written
    specs. The validator judges the model: one SpecError repair turn, then
    an honest 422 carrying the last refusal."""
    _guard(request)
    body = await request.json()
    prompt = str(body.get("prompt", "")).strip()
    if not prompt:
        raise HTTPException(400, "prompt required")
    if len(prompt) > 500:
        raise HTTPException(400, "prompt too long (500 chars)")
    import frames_agent
    try:
        manifest = await run_in_threadpool(frames_agent.prompt_to_frames, prompt)
    except sticky_frames.SpecError as e:
        _log_activity("frames_prompt", prompt=prompt[:120], error=str(e)[:200])
        raise HTTPException(422, str(e))
    except Exception as e:  # boto3/creds/network — name it, don't 500 blindly
        raise HTTPException(502, f"agent turn failed: {type(e).__name__}: {str(e)[:200]}")
    _log_activity("frames_prompt", seq_id=manifest["seq_id"],
                  prompt=f"prompt→frames: {prompt[:120]} "
                         f"({manifest['count']}f, {manifest['attempts']} attempt(s))")
    return manifest


@app.get("/api/glass")
async def api_glass(request: Request):
    """Glass etiquette state — is another client holding an announced window
    on the panel right now? The UI shows this instead of letting paint
    buttons fail mysteriously with 409s."""
    _guard(request)
    w = glass_etiquette.open_window()
    if w:
        # marquee color markup ({cyan}word{/}) is for the TUI, not this UI
        w = dict(w, text=re.sub(r"\{/?[a-z]*\}", "", w["text"]))
    return {"busy": bool(w), "window": w,
            "note": "paint verbs will 409 until this window closes or its "
                    "10min TTL expires; force:true overrides (logged)"
                    if w else "glass free"}


@app.get("/api/frames/library")
async def api_frames_library(request: Request):
    """The sequence shelf — every rendered anim/photo/video sequence on
    disk, newest first, with enough to premiere it from a UI: seq_id,
    frames, cadence, mirrored?, preview URL."""
    _guard(request)
    rows = []
    base = sticky_frames.ANIM_DIR
    if base.is_dir():
        for d in base.iterdir():
            if not re.fullmatch(r"[0-9a-f]{16}", d.name):
                continue
            m = sticky_frames.load_manifest(d.name)
            if not m:
                continue
            rows.append({"seq_id": d.name, "count": m.get("count"),
                         "interval_ms": m.get("interval_ms"),
                         "format": m.get("format"),
                         "mirrored": (d / "mirror.json").is_file(),
                         "preview": f"/api/frames/{d.name}/0.png",
                         "mtime": int(d.stat().st_mtime)})
    rows.sort(key=lambda r: -r["mtime"])
    return {"sequences": rows[:24]}


@app.get("/api/frames/verdict")
async def api_frames_verdict(request: Request, wait_s: float = 20.0):
    """The player testifies (fw 0.19.2+): `play status` returns the LAST
    stream verdict — did frames actually paint, or die pre-frame-0? This
    is the dashboard side of closing the play/paint honesty gap: after
    one-click play, ask the device what really happened. Probe, not paint
    — exempt from glass etiquette."""
    _guard(request)
    adv = await run_in_threadpool(_advertised_verbs)
    if "play" not in adv:
        raise HTTPException(501, "device does not advertise `play` yet")
    out = await run_in_threadpool(_do_invoke, "play", "status",
                                  min(wait_s, 60.0))
    return {"verdict": out}


@app.get("/api/frames/{seq_id}/probe")
async def api_frames_probe(seq_id: str, request: Request):
    """Transport forensics — verify a mirrored sequence EXACTLY as the
    firmware's fetch would (GET, status 200, Content-Length == frame_bytes,
    bytes == local rail). Rules the transport in or out of a white-paint
    premiere in one call. NOTE: the media host 404s HEAD — probe with GET
    only (a curl -I here lies)."""
    _guard(request)
    m = sticky_frames.load_manifest(seq_id)
    cache = sticky_frames.ANIM_DIR / seq_id / "mirror.json"
    if m is None or not cache.is_file():
        raise HTTPException(404, "sequence not mirrored yet — POST /mirror first")
    mirror = json.loads(cache.read_text())

    def _probe() -> dict:
        import requests as rq
        rows, ok = [], True
        for n, url in enumerate(mirror["frames"]):
            r = rq.get(url, timeout=15)
            clen = r.headers.get("content-length")
            local = sticky_frames.frame_path(seq_id, f"{n}.raw").read_bytes()
            same = r.content == local
            good = r.status_code == 200 and clen == str(m["frame_bytes"]) and same
            ok &= good
            rows.append({"frame": n, "status": r.status_code,
                         "content_length": clen, "bytes": len(r.content),
                         "byte_identical": same, "ok": good})
        man = rq.get(mirror["manifest_url"], timeout=15)
        return {"seq_id": seq_id, "transport_ok": ok,
                "manifest": {"status": man.status_code,
                             "frames": len(man.json().get("frames", []))},
                "frames": rows,
                "verdict_hint": "transport clean — suspect blit/refresh side"
                                if ok else "transport FAULTY — fix mirror first"}

    return await run_in_threadpool(_probe)


@app.post("/api/frames/{seq_id}/mirror")
async def api_frames_mirror(seq_id: str, request: Request):
    """Mirror a sequence through the media rail the firmware ALREADY
    trusts (plugin.tiny.technology) — the first duck premiere died
    pre-frame-0 because the device's host allowlist doesn't include this
    dashboard's origin. Uploads every frame + a rewritten manifest
    (frames[] = media URLs) on the OWNER session; returns {manifest_url}
    ready for `play <manifest_url>`. Idempotent: the mirror map is cached
    beside the frames (mirror.json)."""
    _guard(request)
    if not re.fullmatch(r"[0-9a-f]{16}", seq_id):
        raise HTTPException(404, "no such sequence")
    m = sticky_frames.load_manifest(seq_id)
    if m is None:
        raise HTTPException(404, "no such sequence")
    cache = sticky_frames.ANIM_DIR / seq_id / "mirror.json"
    if cache.is_file():
        return json.loads(cache.read_text())

    def _mirror() -> dict:
        urls = []
        for n in range(m["count"]):
            p = sticky_frames.frame_path(seq_id, f"{n}.raw")
            if p is None:
                raise HTTPException(500, f"frame {n}.raw missing on disk")
            urls.append(_relay.upload_media(p.read_bytes(),
                                            "application/octet-stream"))
        mirrored = dict(m)
        mirrored["frames"] = urls
        mirrored.pop("previews", None)   # dashboard-origin URLs — useless on-device
        manifest_url = _relay.upload_media(
            json.dumps(mirrored).encode(), "application/json")
        out = {"seq_id": seq_id, "manifest_url": manifest_url,
               "frames": urls, "count": m["count"],
               "play": f"play {manifest_url}"}
        cache.write_text(json.dumps(out, indent=1))
        return out

    try:
        out = await run_in_threadpool(_mirror)
    except RelayError as e:
        raise HTTPException(502, f"media rail refused: {e}")
    _log_activity("frames_mirror", seq_id=seq_id,
                  prompt=f"mirrored {m['count']}f → {out['manifest_url']}")
    return out


@app.post("/api/frames/{seq_id}/play")
async def api_frames_play(seq_id: str, request: Request):
    """One-click 'play on glass': mirror (cached) → invoke `play
    <manifest_url>` on the device. Composes the two rails that premiered
    the duck — the UI never has to know about host allowlists. Body
    (optional): {wait_s}. Refuses politely when the device doesn't
    advertise `play` yet (older firmware)."""
    _guard(request)
    body = await request.json() if (request.headers.get("content-length") or "0") != "0" else {}
    wait_s = min(float(body.get("wait_s", 30)), 120.0)
    mirror = await api_frames_mirror(seq_id, request)   # same guard, cached
    if not body.get("force"):
        w = glass_etiquette.open_window()
        if w:
            raise HTTPException(409, f"glass busy — \"{w['text'][:160]}\". "
                                     f"Pass force:true to paint over it (logged).")
    adv = await run_in_threadpool(_advertised_verbs)
    if "play" not in adv:
        raise HTTPException(501, "device does not advertise `play` yet — "
                                 "OTA the stream-capable firmware first")
    m = sticky_frames.load_manifest(seq_id) or {}
    est_s = int(m.get("count", 0) * m.get("interval_ms", 500) / 1000) + 2
    glass_etiquette.announce(f"glass: dashboard — play {seq_id} (~{est_s}s, "
                             f"{m.get('count', '?')}f); any card stops it")
    out = await run_in_threadpool(_do_invoke, "play",
                                  mirror["manifest_url"], wait_s)
    return {"seq_id": seq_id, "manifest_url": mirror["manifest_url"],
            "device": out}


_premiere_queue: Dict[str, Any] = {"seq_id": None, "queued_at": None,
                                   "state": "idle", "report": None}


@app.post("/api/frames/{seq_id}/premiere-when-free")
async def api_frames_premiere_when_free(seq_id: str, request: Request):
    """Queue the ceremony for the moment the glass frees. Several clients
    share one panel and one of them re-opens windows faster than
    TTLs expire — polite yielding must not mean the act never happens. One
    slot (a premiere queue two deep is a fight, not a queue); polls the
    etiquette gate every 20s for up to 30min, then runs the FULL ceremony.
    GET /api/frames/premiere-queue for state + final report."""
    _guard(request)
    if _premiere_queue["state"] == "waiting":
        raise HTTPException(409, f"queue holds {_premiere_queue['seq_id']} already "
                                 f"(since {_premiere_queue['queued_at']})")
    if not (sticky_frames.ANIM_DIR / seq_id / "mirror.json").is_file():
        raise HTTPException(404, "sequence not mirrored — POST /mirror first")
    _premiere_queue.update({"seq_id": seq_id, "state": "waiting",
                            "queued_at": time.strftime("%H:%M:%SZ", time.gmtime()),
                            "report": None})

    async def _runner():
        deadline = time.time() + 1800
        try:
            while time.time() < deadline:
                if glass_etiquette.open_window() is None:
                    _premiere_queue["state"] = "running"
                    report = await api_frames_premiere(seq_id, request)
                    _premiere_queue.update({"state": "done", "report": report})
                    _log_activity("premiere_queue", seq_id=seq_id,
                                  prompt=f"queued premiere fired: {report.get('verdict', '?')}")
                    return
                await asyncio.sleep(20)
            _premiere_queue["state"] = "expired (glass never freed in 30min)"
        except HTTPException as e:
            # glass got grabbed between poll and announce — requeue once/loop
            if e.status_code == 409 and time.time() < deadline:
                _premiere_queue["state"] = "waiting"
                asyncio.get_event_loop().create_task(_runner())
            else:
                _premiere_queue.update({"state": "failed", "report": {"error": e.detail}})
        except Exception as e:  # noqa: BLE001 — queue must never die silently
            _premiere_queue.update({"state": "failed", "report": {"error": str(e)[:300]}})

    asyncio.get_event_loop().create_task(_runner())
    return {"queued": seq_id, "poll": "GET /api/frames/premiere-queue",
            "behavior": "fires the full receipted ceremony the moment no other "
                        "client holds a glass window; expires after 30min"}


@app.get("/api/frames/premiere-queue")
async def api_frames_premiere_queue(request: Request):
    _guard(request)
    return _premiere_queue


@app.post("/api/frames/{seq_id}/premiere")
async def api_frames_premiere(seq_id: str, request: Request):
    """THE CEREMONY — one call, full receipts. Composes every rail this dashboard
    built: transport probe (all frames, firmware-eye) → glass window announce
    → play → stream verdict → screenshot + frame-match vs the sequence's own
    previews → home restore → window close. Refuses when glass is busy or
    firmware lacks `play`; every stage lands in the report even on failure,
    because a premiere that dies silently is the bug we spent two days on."""
    _guard(request)
    report: Dict[str, Any] = {"seq_id": seq_id, "stages": {}}
    # 0 · preconditions
    w = glass_etiquette.open_window()
    if w:
        raise HTTPException(409, f"glass busy — \"{w['text'][:160]}\"")
    adv = await run_in_threadpool(_advertised_verbs)
    if "play" not in adv:
        raise HTTPException(501, "firmware does not advertise `play` — OTA first")
    mirror = await api_frames_mirror(seq_id, request)
    m = sticky_frames.load_manifest(seq_id) or {}
    count, interval = int(m.get("count", 0)), int(m.get("interval_ms", 500))
    # 1 · transport probe (firmware-eye contract, all frames + manifest)
    probe = await api_frames_probe(seq_id, request)
    report["stages"]["probe"] = {"transport_ok": probe["transport_ok"],
                                 "frames": len(probe.get("frames", [])),
                                 "hint": probe.get("verdict_hint")}
    if not probe["transport_ok"]:
        report["verdict"] = "REFUSED — transport not clean; premiere would lie"
        return report
    est_s = count * interval / 1000 + 2
    glass_etiquette.announce(
        f"glass: dashboard — PREMIERE window OPEN (~{int(est_s + 25)}s): "
        f"probe✓ play {seq_id} ({count}f@{interval}ms), verdict + "
        f"frame-match, home restored after")
    try:
        # 2 · play
        out = await run_in_threadpool(_do_invoke, "play",
                                      mirror["manifest_url"], 45.0)
        report["stages"]["play"] = {"result": str(out.get("result", ""))[:200],
                                    "pending": bool(out.get("pending"))}
        # 3 · let it run, then ask the player to testify
        await asyncio.sleep(min(est_s + 6, 40))
        v = await run_in_threadpool(_do_invoke, "play", "status", 30.0)
        report["stages"]["verdict"] = str(v.get("result", ""))[:300]
        # 4 · screenshot + frame-match against the sequence's own previews
        shot = await run_in_threadpool(_do_invoke, "screenshot", None, 45.0)
        match = await run_in_threadpool(_frame_match, shot, seq_id, count)
        report["stages"]["frame_match"] = match
        # 5 · restore home
        home = await run_in_threadpool(_do_invoke, "page", "home", 30.0)
        report["stages"]["home_restored"] = "ESP_OK" in str(home.get("result", ""))
    finally:
        glass_etiquette.announce(
            f"glass: dashboard — PREMIERE window CLOSED, home restored. "
            f"match={report.get('stages', {}).get('frame_match', {}).get('best_frame', '?')} "
            f"diff={report.get('stages', {}).get('frame_match', {}).get('diff_pct', '?')}%")
    fm = report["stages"].get("frame_match") or {}
    ok = isinstance(fm.get("diff_pct"), (int, float)) and fm["diff_pct"] < 5.0
    report["verdict"] = ("PREMIERE ✓ — glass showed the sequence (screenshot matches "
                         f"frame {fm.get('best_frame')} at {fm.get('diff_pct')}% diff)"
                         if ok else
                         "PREMIERE ✗ — receipts above name the failing stage")
    _log_activity("premiere", seq_id=seq_id, prompt=report["verdict"])
    return report


def _frame_match(shot: Dict[str, Any], seq_id: str, count: int) -> Dict[str, Any]:
    """Screenshot vs each frame preview: greyscale diff at 800×480, best wins."""
    import io
    from PIL import Image
    url = None
    for im in shot.get("images", []) or []:
        url = im.get("url")
    if not url:
        mres = re.search(r"https://\S+\.png", str(shot.get("result", "")))
        url = mres.group(0) if mres else None
    if not url:
        return {"error": "screenshot reply carried no image url"}
    r = requests.get(url, timeout=20)
    r.raise_for_status()
    got = Image.open(io.BytesIO(r.content)).convert("L").resize((800, 480))
    best, best_diff = None, 101.0
    for i in range(count):
        p = sticky_frames.frame_path(seq_id, f"{i}.png")
        if not p or not p.is_file():
            continue
        ref = Image.open(p).convert("L").resize((800, 480))
        gd, rd = got.tobytes(), ref.tobytes()
        diff = sum(1 for a, b in zip(gd, rd) if abs(a - b) > 32) / len(gd) * 100
        if diff < best_diff:
            best, best_diff = i, diff
    if best is None:
        return {"error": "no local previews to match against"}
    return {"best_frame": best, "diff_pct": round(best_diff, 2),
            "note": "diff<5% = that frame was on the glass"}


@app.post("/api/frames/video")
async def api_frames_video(request: Request, file: UploadFile = File(...),
                           format: str = "1bit", interval_ms: int = 1000):
    """Source (3): video clip → ffmpeg-sampled dithered frame sequence.

    Honest contract: this is a TIME-LAPSE of the clip at e-ink cadence
    (≥250ms/frame), never smooth motion — the panel physically cannot.
    Long clips are sampled evenly across their duration, not truncated.
    Same manifest/store as anim-grammar sequences → tiny_stream_play-ready.
    """
    _guard(request)
    data = await file.read()
    if not data:
        raise HTTPException(400, "empty upload")
    if len(data) > 100 * 1024 * 1024:
        raise HTTPException(413, "clip too large (100MB cap)")
    try:
        manifest = await run_in_threadpool(
            sticky_frames.render_video, data, file.filename or "clip",
            format, interval_ms)
    except sticky_frames.SpecError as e:
        raise HTTPException(422, str(e))
    _log_activity("frames_video", seq_id=manifest["seq_id"],
                  prompt=f"video→frames {file.filename} "
                         f"({manifest['count']}f @ {manifest['interval_ms']}ms)")
    return manifest


_ANIM_FRAME_NAME = re.compile(r"^(\d{1,3})\.(raw|png)$|^manifest$|^spec\.json$")


@app.get("/api/frames/{seq_id}/{name}")
async def api_frames_file(seq_id: str, name: str):
    # capability URL: seq_id = sha256(spec)[:16] is the secret — the DEVICE
    # fetches these over plain http_request with no cookie jar, exactly like
    # /frames/<sha> image frames. Strict whitelist, no traversal surface.
    if not re.fullmatch(r"[0-9a-f]{16}", seq_id) or not _ANIM_FRAME_NAME.fullmatch(name):
        raise HTTPException(404, "no such frame")
    if name == "manifest":
        m = sticky_frames.load_manifest(seq_id)
        if m is None:
            raise HTTPException(404, "no such sequence")
        return m
    target = sticky_frames.frame_path(seq_id, name)
    if target is None:
        raise HTTPException(404, "no such frame")
    media = ("application/octet-stream" if name.endswith(".raw")
             else "application/json" if name.endswith(".json") else "image/png")
    # FileResponse sets Content-Length from stat() — exact bytes, no padding.
    return FileResponse(target, media_type=media,
                        headers={"Cache-Control": "public, max-age=31536000, immutable"})


# ── /anim — composer playground (standalone HTML, no build) ────────────
# Serves docs/dashboard/anim.html at /anim and a link to the spec at
# /anim-spec. Client-side only: validates JSON anim specs against the
# vocabulary in docs/research/EINK_ANIMATION.md §4 (blit/card_diff/delay/
# flush), previews on an 800×480 canvas with honest e-ink ghosting
# accumulation (~2%/partial) and full-flush clearing. "Send to device" is
# intentionally disabled: firmware has no anim verb yet — we do NOT invent
# a relay verb here.
ANIM_HTML = HERE / "anim.html"
ANIM_SPEC = HERE.parent / "research" / "EINK_ANIMATION.md"

@app.get("/anim")
async def anim_page():
    if not ANIM_HTML.is_file():
        raise HTTPException(500, "anim.html missing next to server.py")
    return FileResponse(ANIM_HTML, media_type="text/html")

@app.get("/anim-spec")
async def anim_spec():
    if not ANIM_SPEC.is_file():
        raise HTTPException(404, "EINK_ANIMATION.md not found")
    return FileResponse(ANIM_SPEC, media_type="text/markdown; charset=utf-8")


# ── /rtttl — piezo tune composer (standalone HTML, no build) ───────────
# Serves docs/dashboard/rtttl.html at /rtttl. Client-side only: parses
# RTTTL per docs/research/RTTTL_CORPUS.md §1 (defaults d=4/o=6/b=63,
# dotted notes both positions), previews via Web Audio square oscillator
# with the corpus's 10ms inter-note gaps, piano-roll canvas, and the 12
# corpus presets. "Send to device" is intentionally disabled: firmware
# has no chime/play verb yet (commands.md "Not in the firmware") — we do
# NOT invent a relay verb here.
RTTTL_HTML = HERE / "rtttl.html"

@app.get("/rtttl")
async def rtttl_page():
    if not RTTTL_HTML.is_file():
        raise HTTPException(500, "rtttl.html missing next to server.py")
    return FileResponse(RTTTL_HTML, media_type="text/html")


# ── Source (4): ANSI terminal twin — `curl sticky.cagatay.my/duck` ──────
# The SAME sequences /api/frames stores, streamed as 256-gray half-block
# art. PUBLIC ON PURPOSE (read-only demo: no device access, no owner data
# — it reads only content-addressed previews already public as capability
# URLs; the POST that spends CPU stays session-gated). The duck sequence
# is built at startup through the ordinary render_sequence() pipeline.
import ansi as sticky_ansi

try:
    DUCK_SEQ_ID: Optional[str] = sticky_ansi.ensure_duck()
    print(f"🦆 duck sequence ready — seq_id={DUCK_SEQ_ID}")
except Exception as e:                             # server must still boot
    DUCK_SEQ_ID = None
    print(f"🦆 duck render FAILED at startup: {e}")


async def _ansi_stream(seq_id: str):
    """Chunked text/plain: clear-screen + frame, manifest-interval cadence,
    loop to the 60 s cap, close with the one-line signature."""
    try:
        ansi_frames, interval_ms = await run_in_threadpool(
            sticky_ansi.sequence_to_ansi, seq_id)
    except FileNotFoundError as e:
        raise HTTPException(404, str(e))

    async def gen():
        deadline = time.monotonic() + sticky_ansi.STREAM_CAP_S
        yield sticky_ansi.HIDE_CURSOR
        try:
            while True:
                for frame in ansi_frames:
                    if time.monotonic() >= deadline:
                        return
                    yield sticky_ansi.CLEAR + frame
                    await asyncio.sleep(interval_ms / 1000.0)
        finally:
            yield (sticky_ansi.RESET + sticky_ansi.SHOW_CURSOR
                   + "\r\n" + sticky_ansi.SIGNATURE + "\r\n")

    return StreamingResponse(gen(), media_type="text/plain; charset=utf-8",
                             headers={"Cache-Control": "no-store",
                                      "X-Accel-Buffering": "no"})


@app.get("/duck")
async def duck():
    """The marketing duck. Unauthenticated, read-only, ~2 fps, 60 s max."""
    if DUCK_SEQ_ID is None:
        raise HTTPException(503, "duck sequence failed to render at startup")
    return await _ansi_stream(DUCK_SEQ_ID)


@app.get("/ansi/{seq_id}")
async def ansi_stream(seq_id: str):
    """Terminal twin of ANY stored sequence (capability URL — the 16-hex
    seq_id is the secret, exactly like the device's .raw fetches)."""
    if not re.fullmatch(r"[0-9a-f]{16}", seq_id):
        raise HTTPException(404, "no such sequence")
    return await _ansi_stream(seq_id)


# ── /api/settings + /s/<device_id> — scan-to-configure rail ───────────
# Wire contract: firmware ships relay
# verb `config <json>` (tiny_config_merge_json: networks upsert + scalars)
# and `set` (toggles/snooze). NEITHER is advertised yet (probed live
# 2026-08-26: 19 verbs, no config/set) — every write endpoint gates on the
# device's OWN heartbeat-advertised verb list and answers 409 with an
# honest sentence until the firmware OTA lands. The gate self-opens with
# zero dashboard deploy (same capabilities-handshake rule as the buttons).
# SECRET RULE: wifi passwords are key material — the envelope prompt is
# NEVER written to the activity log; a redacted line is logged instead.

_SETTINGS_TOGGLES = {"snooze", "buzzer", "lift_wake", "auto_lock"}  # scalar keys `set` accepts


def _advertised_verbs() -> List[str]:
    """The device's live heartbeat-advertised verb list (empty on failure)."""
    if MOCK:
        return sorted(VALID_COMMANDS)
    try:
        adv = (_resolve_device() or {}).get("capabilities")
        if isinstance(adv, str):
            adv = json.loads(adv)
        return [str(v) for v in adv] if isinstance(adv, list) else []
    except (RelayError, ValueError):
        return []


def _settings_gate() -> Dict[str, Any]:
    adv = _advertised_verbs()
    has_config, has_set = "config" in adv, "set" in adv
    return {
        "config_ready": has_config, "set_ready": has_set,
        "fw": _last_fw.get("fw"),
        "reason": None if (has_config and has_set) else
        "firmware not ready: the running build does not advertise "
        + " or ".join(v for v, ok in (("`config`", has_config), ("`set`", has_set)) if not ok)
        + " in its heartbeat — a newer firmware OTA adds it; this page "
          "lights up automatically when the device advertises the verb",
    }


@app.get("/api/settings")
async def settings_info(request: Request):
    _guard(request)
    gate = await run_in_threadpool(_settings_gate)
    dev: Dict[str, Any] = {}
    if MOCK:
        dev = {"id": "mock-sticky", "name": "sticky (software mock)", "online": True}
    else:
        try:
            row = await run_in_threadpool(_resolve_device)
            dev = {k: (row or {}).get(k) for k in ("id", "name", "online", "last_seen")}
        except RelayError as e:
            dev = {"error": str(e)}
    # saved networks: readable only once the fw `config` verb ships a `get`
    # side — until then the page shows the gate sentence, never a fake list
    return {"ok": True, "mode": "mock" if MOCK else "live", "device": dev,
            "gate": gate, "toggles": sorted(_SETTINGS_TOGGLES)}


def _config_invoke(payload: Dict[str, Any], redacted: str, wait_s: float = 30.0) -> Dict[str, Any]:
    """`config <json>` round-trip that keeps key material out of the logs:
    mirrors _do_invoke's envelope path with a redacted activity line."""
    prompt = f"config {json.dumps(payload, separators=(',', ':'))}"
    _log_activity("envelope", direction="out", prompt=redacted)
    if MOCK:
        return {"ok": True, "mode": "mock", "result": "mock: config merged", "redacted": redacted}
    out = _relay.invoke(_resolve_device_id(), prompt, wait_s=wait_s)
    _log_activity("reply" if not out.get("pending") else "pending", direction="in",
                  prompt=redacted, result=str(out.get("result", ""))[:300],
                  envelope_id=out.get("envelope_id"))
    if out.get("pending"):
        _track_pending(out.get("envelope_id"), redacted, None)
    return {"ok": True, "mode": "live", **out}


@app.post("/api/settings/wifi")
async def settings_wifi_add(request: Request):
    _guard(request)
    body = await request.json()
    ssid = str(body.get("ssid", "")).strip()
    password = str(body.get("password", ""))
    if not ssid:
        raise HTTPException(400, "ssid required")
    if len(ssid.encode()) > 32 or len(password.encode()) > 63:
        raise HTTPException(400, "ssid ≤32 bytes, password ≤63 bytes (802.11 limits)")
    gate = await run_in_threadpool(_settings_gate)
    if not gate["config_ready"]:
        raise HTTPException(409, gate["reason"])
    payload = {"networks": [{"ssid": ssid, "password": password}]}
    redacted = f"config wifi_add ssid={ssid!r} password=<redacted:{len(password)} chars>"
    try:
        return await run_in_threadpool(_config_invoke, payload, redacted)
    except RelayError as e:
        _log_activity("error", prompt=redacted, error=str(e))
        raise HTTPException(e.status or 424, str(e))


@app.delete("/api/settings/wifi/{ssid}")
async def settings_wifi_forget(ssid: str, request: Request):
    _guard(request)
    ssid = ssid.strip()
    if not ssid:
        raise HTTPException(400, "ssid required")
    gate = await run_in_threadpool(_settings_gate)
    if not gate["config_ready"]:
        raise HTTPException(409, gate["reason"])
    payload = {"networks": [{"ssid": ssid, "forget": True}]}
    redacted = f"config wifi_forget ssid={ssid!r}"
    try:
        return await run_in_threadpool(_config_invoke, payload, redacted)
    except RelayError as e:
        _log_activity("error", prompt=redacted, error=str(e))
        raise HTTPException(e.status or 424, str(e))


@app.post("/api/settings/toggle")
async def settings_toggle(request: Request):
    _guard(request)
    body = await request.json()
    key = str(body.get("key", "")).strip()
    value = body.get("value")
    if key not in _SETTINGS_TOGGLES:
        raise HTTPException(400, f"unknown toggle '{key}' — one of {sorted(_SETTINGS_TOGGLES)}")
    if not isinstance(value, (bool, int)):
        raise HTTPException(400, "value must be boolean/int")
    gate = await run_in_threadpool(_settings_gate)
    if not gate["set_ready"]:
        raise HTTPException(409, gate["reason"])
    prompt = f"set {key} {1 if value else 0}"
    try:
        return await run_in_threadpool(_do_invoke_raw_settings, prompt)
    except RelayError as e:
        _log_activity("error", prompt=prompt, error=str(e))
        raise HTTPException(e.status or 424, str(e))


def _do_invoke_raw_settings(prompt: str, wait_s: float = 30.0) -> Dict[str, Any]:
    """`set k v` has no secrets — normal logging."""
    _log_activity("envelope", direction="out", prompt=prompt)
    if MOCK:
        return {"ok": True, "mode": "mock", "result": f"mock: {prompt} applied"}
    out = _relay.invoke(_resolve_device_id(), prompt, wait_s=wait_s)
    _log_activity("reply" if not out.get("pending") else "pending", direction="in",
                  prompt=prompt, result=str(out.get("result", ""))[:300],
                  envelope_id=out.get("envelope_id"))
    if out.get("pending"):
        _track_pending(out.get("envelope_id"), prompt, None)
    return {"ok": True, "mode": "live", **out}


SETTINGS_HTML = HERE / "settings.html"


@app.get("/s/{device_id}")
async def settings_page(device_id: str):
    # page itself is public-shaped (it holds no data); every API call it
    # makes is WebAuthn-guarded, and the page redirects to enrollment/login
    # via /auth/status client-side — same trust model as the SPA shell
    if not SETTINGS_HTML.is_file():
        raise HTTPException(500, "settings.html missing next to server.py")
    return FileResponse(SETTINGS_HTML, media_type="text/html")


# ── /api/glass — server-rendered e-ink components (glass/, glass_routes.py)
from glass_routes import glass_router  # noqa: E402
app.include_router(glass_router)

# ── SPA ────────────────────────────────────────────────────────────────
DIST = HERE / "frontend" / "dist"
if DIST.exists():
    app.mount("/assets", StaticFiles(directory=DIST / "assets"), name="assets")

    @app.get("/{path:path}")
    async def spa(path: str):
        # CONTAINMENT (found 2026-08-26): starlette decodes
        # %2e%2e BEFORE routing, so `path` can carry ../ segments — DIST/path
        # then resolves OUTSIDE dist and this route served ANY file on disk
        # (proven: .sticky_auth.json with the jwt_secret, through the public
        # tunnel). Resolve and require the dist prefix before touching disk.
        target = (DIST / path).resolve()
        if path and target.is_file() and target.is_relative_to(DIST.resolve()):
            return FileResponse(target)
        return FileResponse(DIST / "index.html")
else:
    @app.get("/")
    async def placeholder():
        return HTMLResponse(
            "<h1>🧲 sticky dashboard</h1><p>backend is up (mode: "
            + ("mock" if MOCK else "live")
            + ") — frontend not built yet: <code>cd frontend && npm run build</code></p>")


if __name__ == "__main__":
    import uvicorn

    kw: Dict[str, Any] = {}
    if os.getenv("STICKY_TLS", "").lower() in ("1", "true", "yes"):
        import tls
        pair = tls.ensure_cert()
        if pair:
            kw = {"ssl_certfile": pair[0], "ssl_keyfile": pair[1]}
            print(f"🔒 TLS on — {tls.access_urls(PORT)}")
    print(f"🧲 sticky dashboard — mode={'mock' if MOCK else 'live'} "
          f"device={DEVICE_SELECTOR or '(mock)'} port={PORT}")
    uvicorn.run(app, host=os.getenv("STICKY_DASH_HOST", "127.0.0.1"), port=PORT, **kw)
