#!/usr/bin/env python3
"""
tiny_relay — owner-side client for the tiny.technology device relay.

The Sticky is not on a LAN socket; it polls the relay. The OWNER side
(this dashboard) mirrors what `use_device` / the tiny CLI do:

    send    POST /api/devices/relay {toDevice, payload}   → {ok, id}
    poll    GET  /api/devices/relay?inReplyTo=<id>        → {ok, reply?}
    list    GET  /api/devices                             → {ok, devices}

Auth: the SAME credential the tiny CLI holds — ~/.tiny/credentials.json
{version, apiUrl, token (Bearer JWT), user{...}, expires}. Path override:
env TINY_CREDENTIALS.

Async contract (mirrors tiny-tech/src/agent/device-invoke.ts): a slow
device is NOT a failure — invoke waits ≤45 s (poll every 3 s), then hands
back {pending, envelope_id}; the mailbox keeps replies ~24 h and
`result(envelope_id)` redeems later.

Media rule (isDeviceMediaUrl): only https://plugin.tiny.technology/media/<name>
(or TINY_WORKER_URL origin) may be surfaced as an image — anything else in a
reply is refused rather than proxied.
"""
from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlsplit

import requests

CREDS_PATH = Path(os.getenv("TINY_CREDENTIALS", "~/.tiny/credentials.json")).expanduser()
MEDIA_ORIGINS = [o for o in ("https://plugin.tiny.technology", os.getenv("TINY_WORKER_URL", "")) if o]
_MEDIA_PATH = re.compile(r"^/media/[A-Za-z0-9._-]+$")


class RelayError(RuntimeError):
    def __init__(self, message: str, status: int = 0, retryable: bool = False):
        super().__init__(message)
        self.status = status
        self.retryable = retryable


def is_device_media_url(raw: Any) -> bool:
    """Port of device-invoke.ts isDeviceMediaUrl — the allowlist for images."""
    if not isinstance(raw, str) or not raw:
        return False
    try:
        u = urlsplit(raw)
    except ValueError:
        return False
    if u.scheme != "https":
        return False
    origin = f"{u.scheme}://{u.netloc}"
    if not any(origin == f"{urlsplit(o).scheme}://{urlsplit(o).netloc}" for o in MEDIA_ORIGINS):
        return False
    return bool(_MEDIA_PATH.match(u.path))


class TinyRelay:
    def __init__(self, creds_path: Path = CREDS_PATH):
        self.creds_path = creds_path
        self._creds: Optional[Dict[str, Any]] = None

    # ── credential handling ───────────────────────────────────────────
    def _load_creds(self) -> Dict[str, Any]:
        if self._creds is None:
            if not self.creds_path.exists():
                raise RelayError(
                    f"no owner credential at {self.creds_path} — run `npx tiny-tech` and log in", 401)
            self._creds = json.loads(self.creds_path.read_text())
        c = self._creds
        exp = c.get("expires")
        if isinstance(exp, (int, float)) and exp < time.time():
            raise RelayError("owner credential expired — re-login with `npx tiny-tech`", 401)
        return c

    @property
    def base_url(self) -> str:
        return str(self._load_creds().get("apiUrl", "https://tiny.technology")).rstrip("/")

    def _headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {self._load_creds()['token']}",
            "Content-Type": "application/json",
            # the Cloudflare WAF 403s bare clients on some routes (/api/media)
            "User-Agent": "sticky-dashboard/1.0 (+tiny.technology relay client)",
        }

    def _request(self, method: str, path: str, **kw) -> Dict[str, Any]:
        try:
            r = requests.request(method, f"{self.base_url}{path}",
                                 headers=self._headers(), timeout=15, **kw)
        except requests.RequestException as e:
            raise RelayError(f"relay unreachable: {e}", 0, retryable=True)
        if r.status_code == 401:
            self._creds = None  # force re-read next time (maybe re-logged-in)
            raise RelayError("tiny.technology refused the owner token (401) — re-login with `npx tiny-tech`", 401)
        try:
            body = r.json()
        except ValueError:
            raise RelayError(f"non-JSON relay answer (HTTP {r.status_code})", r.status_code)
        if r.status_code >= 400 or body.get("ok") is False:
            raise RelayError(str(body.get("error") or f"HTTP {r.status_code}"),
                             r.status_code, retryable=bool(body.get("retryable")))
        return body

    # ── surface ───────────────────────────────────────────────────────
    def list_devices(self) -> List[Dict[str, Any]]:
        return self._request("GET", "/api/devices").get("devices", [])

    def events(self, since_id: int = 0) -> List[Dict[str, Any]]:
        """Owner activity ring — GET /api/events?sinceId=N (session/Bearer authed,
        50 per page, ascending ids, ~200-event cap in D1).

        This is the ONLY owner-side route that surfaces device-originated
        events, and therefore the only way the dashboard can see a REAL
        physical tap: the firmware posts them as kind=device_note with
        detail "<device name>: ui_tap button=N label=…" (the /api/devices/event
        worker allowlist has no ui_tap kind yet).
        """
        body = self._request("GET", f"/api/events?sinceId={max(0, int(since_id))}")
        evs = body.get("events")
        return evs if isinstance(evs, list) else []

    def messages(self, limit: int = 20) -> List[Dict[str, Any]]:
        """Inbox form — GET /api/messages → threads[{userId, login, name, avatar,
        unread, lastBody, lastAttachments, lastAt}], newest first, lastAt in
        unix SECONDS and lastBody already clipped to 140 code points upstream.

        This is the form a poller may call freely. The THREAD form below is not:
        it has a write side effect.
        """
        body = self._request("GET", f"/api/messages?limit={max(1, min(200, int(limit)))}")
        th = body.get("threads")
        return th if isinstance(th, list) else []

    def thread(self, peer: str, limit: int = 20) -> Dict[str, Any]:
        """One conversation — GET /api/messages?with=<login|userId> →
        {peer{userId,login,name,avatar}, messages[{id,direction,body,attachments,
        viaTiny?,read,created}]}, oldest first.

        ⚠️ SIDE EFFECT: the worker marks every inbound message in this thread as
        READ. Never call this from a background poller — it would silently drain
        the unread badge on cagatay's phone. Only call it when a human actually
        opened the thread on the device.
        """
        body = self._request("GET", f"/api/messages?with={peer}&limit={max(1, min(200, int(limit)))}")
        return {"peer": body.get("peer") or {}, "messages": body.get("messages") or []}

    def send_dm(self, to: str, message: str, via_tiny: bool = True) -> Dict[str, Any]:
        """POST /api/messages {to, message, viaTiny} → {ok, id, to{login,name},
        delivered{telegram,push,stored}}.

        `to` is "@login", a login, or a tiny slug. Body cap is 2000 code points
        and the worker REJECTS over-length rather than truncating, so callers
        must not pad. viaTiny marks it as sent by the agent, not typed by
        cagatay — the device must never claim a human wrote it.
        """
        if not isinstance(message, str) or not message.strip():
            raise RelayError("refusing to send an empty message")
        if len(message) > 2000:
            raise RelayError(f"message is {len(message)} code points, cap is 2000")
        return self._request("POST", "/api/messages", json={
            "to": to, "message": message, "viaTiny": bool(via_tiny)})

    def push_card(self, to_device: str, card: Dict[str, Any], wait_s: float = 30.0) -> Dict[str, Any]:
        """Render a card on the device's panel, unprompted.

        This is the notification path: the owner side POSTs an invoke envelope
        holding `render_ui <json>`; the device's 5 s poll picks it up. Two hard
        limits, both learned from the firmware: the envelope payload must be
        ≤8 KB, and the device's HTTP response sink is a 4096-byte buffer that
        DROPS SILENTLY on overflow (tiny_node.cpp) — and because the relay marks
        an envelope delivered as it hands it over, an oversized card is lost for
        good with no reply. So keep card JSON well under 2 KB.
        """
        blob = json.dumps(card, separators=(",", ":"), ensure_ascii=False)
        if len(blob.encode()) > 2048:
            raise RelayError(f"card is {len(blob.encode())} bytes; keep it under 2048 "
                             "or the device's 4 KB response sink drops it silently")
        return self.invoke(to_device, f"render_ui {blob}", wait_s=wait_s)

    def send(self, to_device: str, prompt: str) -> str:
        """Queue an invoke envelope; returns envelope id."""
        body = self._request("POST", "/api/devices/relay", json={
            "toDevice": to_device,
            "payload": {"type": "invoke", "prompt": prompt},
        })
        env_id = body.get("id")
        if not env_id:
            raise RelayError("relay accepted but returned no envelope id")
        return str(env_id)

    def poll(self, envelope_id: str) -> Optional[Dict[str, Any]]:
        """One poll for a reply. Returns parsed reply payload or None."""
        body = self._request("GET", f"/api/devices/relay?inReplyTo={envelope_id}")
        reply = body.get("reply")
        if reply in (None, ""):
            return None
        # device PATCHes a SERIALIZED JSON STRING — decode it here
        payload = reply.get("payload") if isinstance(reply, dict) else reply
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except ValueError:
                payload = {"result": payload}
        return payload if isinstance(payload, dict) else {"result": payload}

    def invoke(self, to_device: str, prompt: str, wait_s: float = 45.0,
               interval_s: float = 3.0) -> Dict[str, Any]:
        """send + wait; on timeout {pending: True, envelope_id}."""
        env_id = self.send(to_device, prompt)
        deadline = time.time() + max(0.0, wait_s)
        while time.time() < deadline:
            time.sleep(min(interval_s, max(0.1, deadline - time.time())))
            payload = self.poll(env_id)
            if payload is not None:
                return {"envelope_id": env_id, **payload}
        return {"envelope_id": env_id, "pending": True}

    def upload_media(self, data: bytes, content_type: str) -> str:
        """POST /api/media on the OWNER session (route accepts session OR
        device token) → the plugin.tiny.technology URL the firmware's host
        allowlist already trusts. Used to mirror frame sequences so `play`
        can fetch them (the dashboard origin is not allowlisted on-device)."""
        from base64 import b64encode
        body = self._request("POST", "/api/media", json={
            "data": b64encode(data).decode(),
            "contentType": content_type,
        })
        url = body.get("url", "")
        if not url.startswith("https://plugin.tiny.technology/"):
            raise RelayError(f"media rail answered an unexpected host: {url[:80]}")
        return url
