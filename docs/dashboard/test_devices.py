"""D1 — multi-device resolver + /api/devices with a mocked relay.
Run: cd docs/dashboard && STICKY_MOCK=0 ~/.tiny/pypi/bin/python -m pytest -q test_devices.py"""
import json
import os
import time

import pytest

os.environ["STICKY_MOCK"] = "0"
os.environ["STICKY_DEVICE"] = "sticky"
os.environ["STICKY_AUTH_ENABLED"] = "false"
os.environ["STICKY_AUTH_STORE"] = "/tmp/test_devices_auth.json"

from fastapi.testclient import TestClient  # noqa: E402

import server  # noqa: E402

NOW = int(time.time())
FLEET = [  # shape recorded from the live relay on 2026-09-07 (capabilities is a JSON STRING)
    {"id": "b20893b4-0000", "name": "sticky", "platform": "esp32s3", "kind": "daemon",
     "last_seen": NOW - 20, "online": True, "capabilities": json.dumps(["status", "say"])},
    {"id": "e318f3fc-0000", "name": "tiny-3096", "platform": "esp32s3", "kind": "daemon",
     "last_seen": NOW - 400, "online": False, "capabilities": "[]"},
    {"id": "69778d71-0000", "name": "tiny-5f51", "platform": "esp32s3", "kind": "daemon",
     "last_seen": None, "online": None, "capabilities": "[]"},
    {"id": "bd707155-0000", "name": "tiny-ddd9", "platform": "nicla-sense", "kind": "daemon",
     "last_seen": NOW - 5, "online": True, "capabilities": "[]"},
    {"id": "2b7f3e0f-0000", "name": "cagatay-mac", "platform": "darwin-arm64", "kind": "cli",
     "last_seen": NOW, "online": True, "capabilities": "[]"},
]


class FakeRelay:
    def __init__(self):
        self.invoked = []

    def list_devices(self):
        return [dict(r) for r in FLEET]

    def invoke(self, device_id, prompt, wait_s=45.0):
        self.invoked.append((device_id, prompt))
        name = next(r["name"] for r in FLEET if r["id"] == device_id)
        return {"result": f"{name} online: fw 0.27.{len(self.invoked)}-t, wifi up, heap 1, battery 4{len(self.invoked)}%, display 800x480 ready",
                "envelope_id": f"env_{len(self.invoked)}"}

    def poll(self, eid):
        return None


@pytest.fixture()
def client(monkeypatch):
    fake = FakeRelay()
    monkeypatch.setattr(server, "_relay", fake)
    server._fleet_cache.update({"rows": [], "ts": 0.0})
    server._device_cache.clear()
    for pd in (server._last_status, server._last_fw, server._last_shot, server._status_refresh):
        pd.all().clear()
    with TestClient(server.app) as c:
        c.fake = fake
        yield c


def test_devices_filters_to_stickies_and_marks_online(client):
    r = client.get("/api/devices")
    assert r.status_code == 200, r.text
    body = r.json()
    names = [d["name"] for d in body["devices"]]
    assert names == ["sticky", "tiny-3096", "tiny-5f51"]  # default first, then by name; no nicla/mac
    by = {d["name"]: d for d in body["devices"]}
    assert by["sticky"]["online"] is True and by["sticky"]["default"] is True
    assert by["tiny-3096"]["online"] is False and by["tiny-3096"]["age_s"] >= 400
    assert by["tiny-5f51"]["online"] is False and by["tiny-5f51"]["last_seen"] is None
    assert body["default"] == "b20893b4-0000"


def test_default_selector_is_env_device(client):
    r = client.get("/api/status")
    assert r.status_code == 200, r.text
    assert client.fake.invoked[-1][0] == "b20893b4-0000"


def test_query_selector_by_name_and_id(client):
    client.get("/api/status?device=tiny-3096")
    assert client.fake.invoked[-1][0] == "e318f3fc-0000"
    client.get("/api/status?device=69778d71-0000")
    assert client.fake.invoked[-1][0] == "69778d71-0000"
    client.get("/api/status", headers={"X-Sticky-Device": "tiny-5f51"})
    assert client.fake.invoked[-1][0] == "69778d71-0000"


def test_exact_name_beats_substring(client):
    # "tiny-" is a substring of two Stickies AND the nicla; exact name wins, then newest
    client.get("/api/status?device=tiny-3096")
    assert client.fake.invoked[-1][0] == "e318f3fc-0000"
    r = client.get("/api/status?device=no-such-device")
    assert r.status_code == 404


def test_caches_are_per_device(client):
    client.get("/api/status?device=sticky")
    client.get("/api/status?device=tiny-3096")
    a = client.get("/api/status/latest?device=sticky").json()["status"]
    b = client.get("/api/status/latest?device=tiny-3096").json()["status"]
    assert a["fw"] != b["fw"], (a, b)
    devs = {d["name"]: d for d in client.get("/api/devices").json()["devices"]}
    assert devs["sticky"]["fw"] == a["fw"] and devs["tiny-3096"]["fw"] == b["fw"]
    assert devs["tiny-5f51"]["fw"] is None  # never probed → no invented number


def test_invoke_body_device(client):
    r = client.post("/api/invoke", json={"command": "status", "device": "tiny-5f51"})
    assert r.status_code == 200, r.text
    assert client.fake.invoked[-1][0] == "69778d71-0000"


def test_pending_envelope_remembers_device(client, monkeypatch):
    def slow_invoke(device_id, prompt, wait_s=45.0):
        client.fake.invoked.append((device_id, prompt))
        return {"pending": True, "envelope_id": "env_slow", "result": ""}
    monkeypatch.setattr(client.fake, "invoke", slow_invoke)
    monkeypatch.setattr(server, "_pending_save", lambda: None)
    client.get("/api/status?device=tiny-3096")
    assert server._pending_envelopes["env_slow"]["device"] == "e318f3fc-0000"
    server._pending_envelopes.pop("env_slow", None)
