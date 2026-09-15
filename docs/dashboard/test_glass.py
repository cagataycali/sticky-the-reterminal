"""GLASS-UI — every registered component renders at panel size, in BOTH
orientations, using ONLY the four palette grays, with its key text legible
(tesseract OCR when installed, else ink-region checks); /api/glass routes
answer through the app with a mocked relay.
Run: cd docs/dashboard && STICKY_MOCK=0 ~/.tiny/pypi/bin/python -m pytest -q test_glass.py"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import time

import pytest
from PIL import Image

os.environ["STICKY_MOCK"] = "0"
os.environ["STICKY_DEVICE"] = "sticky"
os.environ["STICKY_AUTH_ENABLED"] = "false"
os.environ["STICKY_AUTH_STORE"] = "/tmp/test_glass_auth.json"

import glass  # noqa: E402
from glass.canvas import PALETTE  # noqa: E402

TESSERACT = shutil.which("tesseract")

# component → (params, words that must survive OCR in landscape). CASES use fixed dates so
# the fixture (DAILY/WEEKLY rules) renders the same picture every day.
CASES = {
    "weather_bar": ({"demo": 1}, ["Demo City", "Clear", "HIGH", "SUNSET", "NEXT 12 HOURS"]),
    "calendar_month": ({"demo": 1, "date": "2026-09-07", "tz": "America/New_York"}, ["September", "2026", "NEXT", "Standup", "Climbing"]),
    "notifications": ({"demo": 1}, ["Inbox", "Standup", "ada", "tiny-3096"]),
    "now": ({"demo": 1, "clock": "07:42", "tz": "America/New_York"}, ["07:42", "Monday", "Clear", "NEXT", "Run", "THEN"]),
    "fleet": ({"demo": 1}, ["Fleet", "GLASS", "sticky", "SENSORS", "PHONES", "never"]),
    "sun": ({"demo": 1, "clock": "15:40"}, ["Sun", "06:29", "19:18", "DAY LENGTH", "12 h 50 min", "TOMORROW"]),
    "week": ({"demo": 1, "tz": "America/New_York"}, ["Week", "TODAY", "TUE", "SUN", "% rain"]),
    "github": ({"demo": 1}, ["GitHub", "contributions", "TODAY", "STREAK", "LATEST", "open PRs"]),
    "moon": ({"date": "2026-09-07", "time": "21:00"}, ["Moon", "Waning crescent", "NEXT", "New moon", "Full moon"]),
    "air": ({"demo": 1}, ["Air", "Moderate", "UV TODAY", "PM2.5", "WHO"]),
    "habits": ({"demo": 1}, ["Habits", "Run", "Read 20 pages", "Ship one commit", "today"]),
    "poster": ({"mode": "word", "index": 1}, ["WORD OF THE DAY", "petrichor", "earthy smell", "NOUN"]),
    "countdown": ({"demo": 1}, ["COUNTDOWN", "23", "Fomo demo day", "ALSO AHEAD", "SINCE", "of the way"]),
    "clock": ({"face": "analog", "clock": "10:08", "date": "2026-09-07"}, ["10:08", "Monday", "WEEK", "37", "250 of 365"]),
    "photo": ({}, ["Tetons", "Snake River", "Ansel Adams"]),
    "compose": ({"demo": 1, "preset": "morning", "clock": "07:40"}, ["MORNING", "NEXT", "AIR", "HABITS", "MOON"]),
    "arm": ({"demo": 1}, ["LEADER ARM", "home", "cannot lift", "shoulder", "elbow", "wrist"]),
    "goals": ({"demo": 1}, ["done", "Steps", "Water", "Deep work", "Reading", "to go"]),
    "printer": ({"demo": 1}, ["Bambu X2D", "63", "done at", "layer 148 of 236", "SPOOLS"]),
    "budget": ({"demo": 1}, ["September 2026", "554", "over pace", "groceries", "projected"]),
    "departures": ({"demo": 1}, ["Bedford Av", "Manhattan", "leave in 3 min", "Queens Plaza", "weekend service"]),
    "almanac": ({"demo": 1}, ["7 September", "day 250", "1159", "Born", "Observed"]),
    "tide": ({"demo": 1}, ["The Battery", "17:54", "1.67", "00:35", "range"]),
    "playing": ({"demo": 1}, ["PLAYING", "Petrichor", "The Quiet Desk", "1:47", "volume"]),
    "chess": ({"demo": 1, "date": "2026-09-07"}, ["Daily puzzle", "Black to move", "Mate in 2", "1714", "Kg3"]),
    "year": ({"demo": 1, "date": "2026-09-07"}, ["2026", "250", "of 365 days", "115", "22 Sep"]),
    "markets": ({"demo": 1, "at": "2026-09-04 16:00"}, ["Markets", "Apple", "319.97", "Bitcoin", "52"]),
    "clocks": ({"demo": 1, "at": "2026-09-07 09:40"}, ["CLOCKS", "Newark", "Bangalore", "+13", "Best window 10"]),
    "sudoku": ({"date": "2026-09-07"}, ["SUDOKU", "Medium", "32 clues", "CHECK CODE", "row, column"]),
    "reading": ({"demo": 1, "date": "2026-09-07"}, ["READING", "Design of Everyday", "Don Norman", "246", "368", "67", "finishes", "LAST 14 DAYS", "UP NEXT", "FINISHED THIS YEAR"]),
    "agenda": ({"demo": 1, "date": "2026-09-07"}, ["Next 7 days", "events", "booked", "Mon", "Sun", "free"]),
    "overnight": ({"demo": 1}, ["TOMORROW", "Tuesday", "Run", "events", "sunrise", "DUE TOMORROW", "THEN", "lights out"]),
    "todo": ({"demo": 1}, ["Today", "FIRST", "Flip", "public", "TODAY", "TOMORROW", "DONE"]),
    "focus": ({"demo": 1, "clock": "11:20"}, ["FOCUS", "CALENDAR", "focus", "sessions", "Glass components", "FREE AHEAD"]),
    "calendar_day": ({"demo": 1, "date": "2026-09-07", "tz": "America/New_York"}, ["Monday", "Standup", "Lunch with Ada", "Sticky launch week", "events"]),
}


def ocr(im: Image.Image) -> str:
    if not TESSERACT:
        return ""
    import io
    buf = io.BytesIO()
    im.save(buf, "PNG")   # stdin/stdout: leptonica cannot open /tmp paths from a sandboxed child here
    out = subprocess.run([TESSERACT, "stdin", "stdout", "--psm", "11"], input=buf.getvalue(),
                         capture_output=True, timeout=60)
    return " ".join(out.stdout.decode("utf-8", "replace").split())


def ink_fraction(im: Image.Image) -> float:
    hist = im.histogram()
    return 1 - hist[255] / (im.width * im.height)


@pytest.mark.parametrize("name", sorted(glass.load_all()))
@pytest.mark.parametrize("orientation", ["landscape", "portrait"])
def test_render_size_and_palette(name, orientation):
    params, _ = CASES.get(name, ({"demo": 1}, []))
    im = glass.render(name, params, orientation)
    assert im.mode == "L"
    assert im.size == ((800, 480) if orientation == "landscape" else (480, 800))
    used = {c for _, c in im.getcolors(256)}
    assert used <= set(PALETTE), f"off-palette grays: {sorted(used - set(PALETTE))}"
    assert len(used) <= 4
    if name == "photo":
        assert 0.1 < ink_fraction(im) < 0.95, "a dithered photograph is mostly ink; only a blank or solid frame is wrong"
    else:
        assert 0.02 < ink_fraction(im) < 0.6, "a component is neither blank nor a black slab"


@pytest.mark.parametrize("name", sorted(CASES))
def test_key_text_present(name):
    params, words = CASES[name]
    im = glass.render(name, params, "landscape")
    if TESSERACT:
        text = ocr(im)
        missing = [w for w in words if w.lower() not in text.lower()]
        assert not missing, f"OCR missed {missing} in: {text[:300]}"
    else:  # no OCR: the header band and the hero band must carry ink
        assert ink_fraction(im.crop((24, 24, 400, 60))) > 0.01
        assert ink_fraction(im.crop((24, 80, 460, 260))) > 0.05


def test_calendar_adapter_expands_rrule_and_overlaps():
    from glass.adapters import calendar as cal
    d = cal.fetch_day({"demo": 1, "date": "2026-09-07", "tz": "America/New_York"})  # a Monday
    titles = [e["title"] for e in d["events"]]
    assert "Standup" in titles and "Climbing" in titles and "1:1 with Sam" in titles  # DAILY, WEEKLY BYDAY
    assert [e["title"] for e in d["all_day"]] == ["Sticky launch week"]
    tue = cal.fetch_day({"demo": 1, "date": "2026-09-08", "tz": "America/New_York"})
    assert "Climbing" not in [e["title"] for e in tue["events"]]  # MO,WE,FR only
    from glass.calendar_day import _lanes
    lanes = {e["title"]: (lane, n) for e, lane, n in _lanes(d["events"])}
    assert lanes["Firmware review — grammar v12 cards"][1] == 2 and lanes["1:1 with Sam"][1] == 2  # 11:00 overlap
    assert lanes["Standup"] == (0, 1)
    m = cal.fetch_month({"demo": 1, "date": "2026-09-07", "tz": "America/New_York"})
    assert m["month_label"] == "September 2026" and len(m["by_day"]["2026-09-07"]) == 9


def test_notifications_adapter_sorts_unread_first_and_accepts_push_items():
    from glass.adapters import notifications as n
    d = n.fetch({"items": [
        {"source": "dm", "from": "a", "text": "old unread", "age_s": 9000, "unread": True},
        {"source": "bogus", "from": "b", "text": "new read", "age_s": 10},
        {"source": "fleet", "from": "c", "text": "newer unread", "age_s": 5, "unread": True},
    ]})
    assert [i["from"] for i in d["items"]] == ["c", "a", "b"] and d["unread"] == 2
    assert d["items"][2]["source"] == "system"  # unknown source folds into system
    assert glass.render("notifications", {"items": []}, "landscape").size == (800, 480)  # empty state renders


def test_fleet_adapter_groups_and_presence():
    from glass.adapters import fleet as fl
    d = fl.fetch({"demo": 1})
    assert [g["key"] for g in d["groups"]] == ["glass", "sensors", "computers", "phones", "endpoints"]
    assert d["online"] == 4 and d["total"] == 13
    assert fl.group_of("esp32s3", "daemon") == "glass" and fl.group_of("bambu-x2d", "endpoint") == "endpoints"
    assert d["devices"][0]["online"] and d["devices"][-1]["age_s"] is None  # online first, never-seen last


def test_sun_adapter_geometry():
    from glass.adapters import sun as sd
    day = sd.fetch({"demo": 1, "clock": "12:53"})
    assert day["phase"] == "day" and abs(day["progress"] - 0.5) < 0.01 and day["noon"] == "12:53"
    assert day["delta"].startswith("−3 min") and day["next"]["what"] == "sunset"
    night = sd.fetch({"demo": 1, "clock": "03:00"})
    assert night["phase"] == "night" and night["next"]["what"] == "sunrise" and 0 < night["progress"] < 1
    assert glass.render("sun", {"demo": 1, "clock": "22:30"}, "portrait").size == (480, 800)


def test_week_adapter_joins_weather_and_calendar():
    from glass.adapters import week as wk
    d = wk.fetch({"demo": 1, "tz": "America/New_York"})
    assert len(d["days"]) == 7 and d["days"][0]["dow"] == "Today"
    assert d["week_lo"] <= min(x["lo"] for x in d["days"]) and d["total_events"] > 0
    assert d["days"][0]["n_events"] > 0 and d["days"][0]["titles"]


def test_github_adapter_grid_and_streak():
    from glass.adapters import github as gh
    d = gh.fetch({"demo": 1})
    assert len(d["weeks"]) == 20 and all(len(w) == 7 for w in d["weeks"])
    assert d["today"] == 47 and d["streak"] == 2 and d["best"]["count"] == 47
    labels = [i for i, m in enumerate(d["months"]) if m]
    assert all(b - a >= 3 for a, b in zip(labels, labels[1:]))  # month labels never collide
    assert d["events"][0]["ago"].endswith("min")


def test_moon_arithmetic_and_disc():
    from glass.adapters import moon as m
    from glass.moon import draw_moon
    from glass.canvas import Glass, WHITE, BLACK
    full = m.fetch({"date": "2026-09-26", "time": "21:00"})      # a full moon by the mean cycle
    assert full["name"] == "Full moon" and full["illumination"] > 0.98
    new = m.fetch({"date": "2026-09-11", "time": "21:00"})
    assert new["name"] == "New moon" and new["illumination"] < 0.02
    q1 = m.fetch({"date": "2026-09-19", "time": "21:00"})
    assert q1["waxing"] and 0.4 < q1["illumination"] < 0.6
    g = Glass((200, 200)); draw_moon(g, 100, 100, 80, 0.25, maria=False)
    px = g.im.load()
    assert px[140, 100] == WHITE and px[60, 100] == BLACK      # waxing: lit on the right
    g = Glass((200, 200)); draw_moon(g, 100, 100, 80, 0.75, maria=False)
    px = g.im.load()
    assert px[60, 100] == WHITE and px[140, 100] == BLACK      # waning: lit on the left


def test_air_adapter_categories_and_guidelines():
    from glass.adapters import air
    d = air.fetch({"demo": 1})
    assert d["aqi"] == 54 and d["aqi_label"] == "Moderate"
    assert d["uv_peak_at"] == "13:00" and d["protect"] == ("10:00", "15:00")
    no2 = next(p for p in d["pollutants"] if p["name"] == "NO2")
    assert no2["ratio"] > 1 and d["pollen_note"]
    assert air.aqi_category(151) == "Unhealthy" and air.uv_category(8) == "Very high"


def test_habits_adapter_streaks_and_params():
    from glass.adapters import habits as hb
    d = hb.fetch({"demo": 1})
    ship = next(h for h in d["habits"] if h["name"] == "Ship one commit")
    assert ship["streak"] == 30 and ship["rate"] == 1.0 and d["done_today"] == 4
    d2 = hb.fetch({"habits": [{"name": "X", "days": "1110"}, {"name": "Y", "days": "111"}], "date": "2026-09-07", "days": 7})
    assert d2["n"] == 7 and d2["source"] == "params"
    assert d2["habits"][0]["streak"] == 3            # today open → counted from yesterday
    assert d2["habits"][1]["streak"] == 3 and d2["habits"][1]["marks"][:4] == [False] * 4   # left-padded
    assert d2["totals"][-1] == 1


def test_poster_modes_and_rotation():
    from glass.adapters import poster as po
    import glass
    a = po.fetch({"mode": "word", "date": "2026-09-07"})
    b = po.fetch({"mode": "word", "date": "2026-09-08"})
    assert a["text"] != b["text"] and a["kicker"] == "Word of the day"
    assert "·" in a["pron"] and "ˈ" not in a["pron"]          # respelling, never IPA (no glyphs)
    ln = po.fetch({"mode": "line", "index": 6})
    assert ln["by"] == "Robert Frost"
    t = po.fetch({"text": "Back at 3", "sub": "lunch"})
    assert t["mode"] == "text" and t["sub"] == "lunch"
    for m in ({"mode": "line", "index": 6}, {"text": "Back at 3", "sub": "lunch", "by": "c"}):
        for o in ("landscape", "portrait"):
            im = glass.render("poster", m, o)
            assert im.size == ((800, 480) if o == "landscape" else (480, 800))


def test_countdown_adapter_roll_and_since():
    from glass.adapters import countdown as cdn
    d = cdn.fetch({"demo": 1})
    assert d["hero"]["days"] == 23 and d["hero"]["kind"] == "to" and abs(d["hero"]["progress"] - 6 / 29) < 0.01
    bday = next(u for u in d["upcoming"] if "birthday" in u["label"])
    assert bday["recurs"] and bday["date"] == "2026-11-02"
    assert [s["days"] for s in d["since"]] == [26, 65]
    one = cdn.fetch({"to": "2026-12-25", "label": "Christmas", "date": "2026-09-07"})
    assert one["hero"]["days"] == 109 and one["source"] == "params" and one["upcoming"] == []
    rolled = cdn.fetch({"events": [{"label": "b", "date": "2020-02-29", "every": "year"}], "date": "2026-09-07"})
    assert rolled["hero"]["date"] == "2027-02-28"
    past = cdn.fetch({"events": [{"label": "gone", "date": "2026-01-01"}], "date": "2026-09-07"})
    assert past["hero"]["kind"] == "since"


def test_clock_faces_and_zones():
    from glass.adapters import clock as ck
    import glass
    d = ck.fetch({"clock": "10:08", "date": "2026-09-07", "tz": "America/New_York", "zones": "Europe/Istanbul,Asia/Tokyo,Nope/Zone"})
    assert d["week"] == 37 and d["doy"] == 250 and d["days_in_year"] == 365
    assert [z["city"] for z in d["zones"]] == ["Istanbul", "Tokyo"]           # bad zone dropped
    assert d["zones"][0]["time"] == "17:08" and d["zones"][0]["delta"] == 7 and d["zones"][1]["time"] == "23:08"
    assert d["zones"][1]["is_day"] is False and d["zones"][0]["is_day"] is True
    for face in ("digits", "world", "minimal"):
        for o in ("landscape", "portrait"):
            assert glass.render("clock", {"face": face, "clock": "10:08", "date": "2026-09-07"}, o).size[0] > 0


def test_photo_dither_palette_and_source_guard():
    import glass
    from glass.adapters import photo as ph
    from glass.photo import dither4, cover
    from glass.canvas import PALETTE
    from PIL import Image
    im = glass.render("photo", {}, "landscape")
    vals = {c for _, c in im.getcolors(256)}
    assert vals <= set(PALETTE) and len(vals) == 4            # a photo uses all four levels
    ramp = Image.linear_gradient("L").resize((200, 50))
    assert {c for _, c in dither4(ramp).getcolors(256)} <= set(PALETTE)
    c = cover(Image.new("L", (1000, 500), 128), (480, 800), (0.5, 0.5))
    assert c.size == (480, 800)
    for bad in ({"url": "http://example.com/x.jpg"}, {"path": "/etc/passwd"}, {"path": "../../../etc/hosts"}):
        try:
            ph.fetch(bad)
            assert False, bad
        except (ValueError, OSError):
            pass
    fr = glass.render("photo", {"mode": "frame", "caption": "x", "by": "y"}, "portrait")
    assert fr.size == (480, 800)


def test_compose_presets_tiles_and_fallback(monkeypatch):
    import glass
    from glass import compose
    for preset in ("morning", "desk", "evening", "work", "lock"):
        d = compose.fetch({"demo": 1, "preset": preset})
        assert d["rows"] == compose.PRESETS[preset]
        assert all(d["data"][n] is not None for r in d["rows"] for n in r)
        for o in ("landscape", "portrait"):
            assert glass.render("compose", {"demo": 1, "preset": preset}, o).size[0] > 0
    for name in ("goals", "focus", "arm", "todo"):
        assert name in compose.TILES
    # portrait presets stack: lock is one column of clock/next/todo then weather|inbox
    dp = compose.fetch({"demo": 1, "preset": "lock"})
    assert dp["rows_portrait"] == [["clock"], ["next"], ["todo"], ["weather", "inbox"]]
    assert dp["rows"] != dp["rows_portrait"] and "todo" in dp["data"]
    custom = compose.fetch({"demo": 1, "tiles": "clock,word,bogus,inbox"})
    assert custom["rows"] == [["clock", "word", "inbox"]] and custom["preset"] == "custom"
    # a tile whose source dies falls back to its fixture and is flagged demo
    def boom(p):
        if not p.get("demo"):
            raise RuntimeError("api down")
        return {"aqi": 1, "aqi_label": "Good", "uv": 0, "uv_label": "Low", "uv_peak": 0, "uv_peak_at": "", "pollutants": []}
    monkeypatch.setitem(compose.TILES, "air", (boom, compose.TILES["air"][1]))
    d = compose.fetch({"tiles": "air"})
    assert d["data"]["air"]["demo"] is True
    assert glass.render("compose", {"tiles": "air"}, "landscape").size == (800, 480)


def test_library_index_is_current():
    from glass import index_md
    from pathlib import Path
    here = Path(index_md.__file__).parent
    assert (here / "README.md").read_text() == index_md.build_readme(), "run: python -m glass.index_md"
    assert (here / "NATIVE.md").read_text() == index_md.build_native(), "run: python -m glass.index_md"
    readme = (here / "README.md").read_text()
    for c in glass.components():
        assert f"### {c['name']}" in readme
    assert (here / "previews" / "CONTACT.png").exists()


def test_arm_adapter_and_kinematics(monkeypatch):
    from glass.adapters import arm as ad
    from glass.arm import _angles
    d = ad.fetch({"demo": 1})
    assert d["source"] == "demo" and d["folded"] and not d["can_lift"] and abs(d["volts"] - 5.6) < 0.2
    assert [j["name"] for j in d["joints"]][:4] == ["shoulder_pan", "shoulder_lift", "elbow_flex", "wrist_flex"]
    assert all(abs(j["q"]) < 3 for j in d["joints"])
    a1, a2, a3 = _angles({"lift": 0, "elbow": 0, "wrist": 0, "pan": 0, "tilt": 0})
    assert 160 < a1 < 180 and -10 < a2 < 20                              # folded: lies back, forearm forward
    a1, a2, a3 = _angles({"lift": 81.2, "elbow": -157.4, "wrist": -76.5, "pan": 0, "tilt": 0})
    assert 80 < a1 < 100 and 70 < a2 < 100 and -15 < a3 < 15             # upright: vertical, camera level
    # a dead live endpoint degrades to the fixture
    monkeypatch.setenv("STICKY_ARM_URL", "http://127.0.0.1:9/nope")
    assert ad.fetch({})["source"] == "demo"


def test_goals_adapter_and_params():
    from glass.adapters import goals as gd
    d = gd.fetch({"demo": 1})
    assert d["source"] == "demo" and len(d["goals"]) == 4 and d["done"] == 0
    steps = d["goals"][0]
    assert steps["display"] == "7,842" and steps["remaining_display"] == "2,158" and steps["pct"] == 78
    two = gd.fetch({"goals": '[{"name":"Steps","value":10450,"target":10000},{"name":"Water","value":0.4,"target":2.5,"unit":"L"}]'})
    assert two["source"] == "params" and two["done"] == 1 and two["goals"][1]["remaining_display"] == "2.1 L"
    for n in (1, 2, 3, 4):
        goals = [{"name": f"g{i}", "value": i, "target": 4, "history": [1, 2, 3, 4, 0, 1, i]} for i in range(n)]
        for o in ("landscape", "portrait"):
            im = glass.render("goals", {"goals": goals}, o)
            assert im.size == ((800, 480) if o == "landscape" else (480, 800))


def test_every_glyph_is_legible_at_arms_length():
    """E-ink at 800×480 on a 7-inch panel: nothing set under 11 px, anywhere, in either orientation."""
    from glass.canvas import Glass
    import glass
    too_small = []
    orig = Glass.text

    def spy(self, xy, s, f, fill=0, anchor="la"):
        if str(s).strip() and f.size < 11:
            too_small.append((cur[0], f.size, str(s)[:20]))
        return orig(self, xy, s, f, fill, anchor)

    cur = [""]
    Glass.text = spy
    try:
        glass.load_all()
        for name in glass.REGISTRY:
            params = CASES[name][0] if name in CASES else {"demo": 1}
            for o in ("landscape", "portrait"):
                cur[0] = f"{name}/{o}"
                glass.render(name, params, o)
    finally:
        Glass.text = orig
    assert not too_small, too_small[:12]


def test_ink_budget():
    """Heavy black ghosts on refresh: no component (except the dithered photo) may be more than 40 % BLACK."""
    import numpy as np
    import glass
    glass.load_all()
    heavy = []
    for name in glass.REGISTRY:
        if name in ("photo", "playing"):   # both are dithered pictures by design
            continue
        params = CASES[name][0] if name in CASES else {"demo": 1}
        for o in ("landscape", "portrait"):
            share = (np.array(glass.render(name, params, o)) == 0).mean()
            if share > 0.40:
                heavy.append((name, o, round(share, 3)))
    assert not heavy, heavy


def test_printer_contract_pushed_and_fixture():
    from glass.adapters import printer as pr
    d = pr.fetch({"demo": 1})
    assert d["pct"] == 63 and d["layer_label"] == "layer 148 of 236" and d["remaining_label"] == "47 min" and d["elapsed_label"] == "1 h 21"
    assert d["eta_label"] and len(d["ams"]) == 4 and d["ams"][2]["active"] and d["state"] == "printing"
    p = pr.fetch({"state": "finished", "job": "bracket.gcode", "progress": 1, "layer": 80, "layers": 80, "elapsed_min": 130, "nozzle_c": 31})
    assert p["source"] == "pushed" and p["pct"] == 100 and p["eta_label"] is None and p["ams"] == [] and p["elapsed_label"] == "2 h 10"
    q = pr.fetch({"state": "printing", "progress": 1.7, "remaining_min": 0})
    assert q["progress"] == 1.0 and q["remaining_label"] == "0 min"
    for o in ("landscape", "portrait"):
        assert glass.render("printer", {"state": "idle", "job": "", "ams": "[]"}, o).size[0] > 0     # empty spools/temps must not crash


def test_budget_takes_bills_off_the_top_and_paces_the_rest():
    from glass.adapters import budget as bd
    d = bd.fetch({"demo": 1})
    assert d["day"] == 7 and d["days_in_month"] == 30 and d["fixed"] == 2450.0 and d["budget"] == 4200.0
    assert abs(d["var_budget"] - 1750) < 1e-9 and abs(d["pace"] - 1750 * 7 / 30) < 1e-6
    assert abs(d["variable"] + d["fixed"] - d["spent"]) < 1e-6 and d["delta"] > 0          # the fixture is over pace
    assert [c["day"] for c in d["cumulative"]] == list(range(1, 8)) and abs(d["cumulative"][-1]["total"] - d["variable"]) < 1e-6
    assert d["categories"][0]["name"] == "groceries" and d["recent"][0]["date_label"] == "7 Sep"
    p = bd.fetch({"entries": [{"date": "2026-03-02", "amount": 30, "category": "x"}, {"date": "2026-03-01", "amount": 900, "category": "rent", "fixed": True},
                              {"date": "2026-03-20", "amount": 999, "category": "future"}], "month_budget": 1500, "currency": "€", "at": "2026-03-10"})
    assert p["source"] == "pushed" and p["variable"] == 30 and p["fixed"] == 900 and p["days_in_month"] == 31       # the 20th is not yet spent
    assert p["delta"] < 0 and abs(p["daily_left"] - (600 - 30) / 21) < 1e-6 and p["currency"] == "€"


def test_departures_relative_and_clock_times_become_minutes_and_leave_in():
    import datetime as dt
    from glass.adapters import departures as dp
    d = dp.fetch({"demo": 1})
    assert d["stop"] == "Bedford Av" and d["walk_min"] == 6 and d["lines"][0]["mins"] == [3, 9, 17, 24]
    assert d["lines"][0]["leave_in"] == 3 and d["lines"][2]["kind"] == "bus" and d["lines"][2]["leave_in"] == 2
    now = dt.datetime.now()
    t1 = (now + dt.timedelta(minutes=12)).strftime("%H:%M")
    p = dp.fetch({"stop": "Home", "walk_min": 20, "lines": [{"line": "7", "dest": "Flushing", "times": [t1, "+2", "+40"]}]})
    ln = p["lines"][0]
    assert p["source"] == "pushed" and ln["mins"][0] == 2 and 11 <= ln["mins"][1] <= 12 and ln["leave_in"] == 20   # only the +40 is catchable
    assert dp.fetch({"lines": [{"line": "X", "dest": "Nowhere", "times": []}]})["lines"][0]["leave_in"] is None


def test_compose_weekend_preset_uses_the_new_tiles():
    from glass import compose
    assert compose.PRESETS["weekend"] == [["tide", "tide", "moon"], ["playing", "markets", "word"]]
    for t in ("tide", "playing", "markets"):
        assert t in compose.TILES
    for o in ("landscape", "portrait"):
        im = glass.render("compose", {"demo": 1, "preset": "weekend"}, o)
        assert im.size == ((800, 480) if o == "landscape" else (480, 800))
        txt = ocr(im)
        assert "Petrichor" in txt and "1.67" in txt, txt[:200]


def test_almanac_spreads_the_centuries_and_trims_holidays():
    from glass.adapters import almanac as al
    d = al.fetch({"demo": 1})
    assert d["date_label"] == "7 September" and d["doy"] == 250 and len(d["events"]) == 8
    years = [e["year"] for e in d["events"]]
    assert years == sorted(years) and years[0] == 1159 and years[-1] == 2011      # first and last of the curated list, spread between
    assert d["births"][0]["year"] == 923 and d["deaths"][0]["year"] == 251
    assert "Independence Day (Brazil)" in d["holidays"] and not any("feast" in h.lower() for h in d["holidays"])
    assert al.fetch({"demo": 1, "n_events": 3})["events"][1]["year"] not in (1159, 2011)


def test_tide_now_is_interpolated_and_marks_agree():
    from glass.adapters import tide as td
    d = td.fetch({"demo": 1})
    assert d["station"]["name"] == "The Battery" and d["unit"] == "m" and d["now"]["t"] == 6.0
    assert d["trend"] == "rising" and 1.1 < d["now"]["v"] < 1.3           # 15:12 between the 11:43 low and 17:54 high
    assert d["next_high"]["time_label"] == "17:54" and d["next_high"]["in_min"] == 162
    assert d["next_low"]["day"] == "tomorrow" and d["next_low"]["time_label"] == "00:35"
    assert [e["type"] for e in d["extremes"]] == ["L", "H", "L", "H"] and abs(d["range"] - 1.498) < 0.01
    assert len(d["points"]) == 48 and all(0 <= p["t"] <= 24 for p in d["points"])
    e = td.fetch({"demo": 1, "at": "2026-09-07 20:00"})
    assert e["trend"] == "falling" and e["next_low"]["time_label"] == "00:35" and e["next_high"]["time_label"] == "06:32"


def test_playing_pushed_track_and_fixture():
    from glass.adapters import playing as pl
    d = pl.fetch({"demo": 1})
    assert d["demo"] and d["track"] == "Petrichor" and abs(d["progress"] - 107 / 248) < 1e-6 and d["art"].endswith("photo.jpg")
    p = pl.fetch({"track": "Blue in Green", "artist": "Miles Davis", "album": "Kind of Blue", "duration_s": 337,
                  "position_s": 400, "state": "paused"})
    assert p["source"] == "pushed" and p["position_s"] == 337 and p["progress"] == 1.0 and p["art"] is None
    from glass.playing import _mmss
    assert _mmss(107) == "1:47" and _mmss(3600) == "60:00"


def test_chess_position_is_after_the_opponents_move():
    from glass.adapters import chess as ch
    d = ch.fetch({"demo": 1, "date": "2026-09-07"})
    assert d["to_move"] == "black" and d["goal"] == "Mate in 2"
    assert d["last"] == {"san": "Kg3", "from": "h2", "to": "g3", "by": "white"}
    assert d["solution_san"] == ["Qh3+", "Kf4", "Qxf3#"]
    # board faces the mover: black's view has h1 top-left, a8 bottom-right, and the white king on g3
    assert d["files"][0] == "h" and d["ranks"][0] == "1"
    assert d["board"][2][d["files"].index("g")] == "K"
    assert sum(1 for row in d["board"] for c in row if c != ".") == 22   # counted off the FEN by hand
    assert "epaulette mate" in d["themes"] and d["url"].endswith("/3ngI1")


def test_year_counts_and_relative_busy():
    from glass.adapters import year as yr
    d = yr.fetch({"demo": 1, "date": "2026-09-07"})
    assert d["ordinal"] == 250 and d["left"] == 115 and d["n"] == 365 and d["weeks_left"] == 16
    assert sum(len(m["days"]) for m in d["months"]) == 365
    assert sum(1 for m in d["months"] for x in m["days"] if x["today"]) == 1
    assert d["season"]["name"] == "autumn" and d["season"]["days"] == 15
    # the fixture has 7–9 events EVERY day: busy must still be a minority, never the whole year
    assert 0 < d["busy_days"] < 120
    assert yr.fetch({"demo": 1, "date": "2026-12-25"})["season"]["name"] == "spring"
    assert yr.fetch({"demo": 1, "date": "2026-09-07", "south": 1})["season"]["name"] == "spring"
    assert yr.fetch({"demo": 1, "date": "2028-03-01"})["n"] == 366


def test_markets_change_is_versus_yesterday_and_formats():
    from glass.adapters import markets as mk
    from glass.markets import fmt_change, fmt_price
    d = mk.fetch({"demo": 1, "at": "2026-09-04 16:00"})
    r = {q["symbol"]: q for q in d["rows"]}
    assert abs(r["AAPL"]["change_pct"] - (-2.51)) < 0.05 and not r["AAPL"]["stale"]
    assert d["up"] + d["down"] == 6 and d["best"]["symbol"] == "NVDA" and d["worst"]["symbol"] == "AAPL"
    assert fmt_price(79457.98) == "79,458" and fmt_price(7718.6) == "7,718.6" and fmt_price(1.1628) == "1.1628"
    assert fmt_change({"change": -8.24, "change_pct": -2.51}) == "−8.24  −2.51 %"
    # explicit symbols + demo → fixture stands in without a network call, unknown symbol still renders a row
    d2 = mk.fetch({"demo": 1, "symbols": "Apple=AAPL,ZZZZ"})
    assert [q["label"] for q in d2["rows"]] == ["Apple", "ZZZZ"] and d2["rows"][1]["symbol"] == "ZZZZ"


def test_clocks_overlap_math():
    from glass.adapters import clocks as ck
    d = ck.fetch({"demo": 1, "at": "2026-09-07 09:40"})
    by = {r["label"]: r for r in d["rows"]}
    assert by["Tokyo"]["time"] == "22:40" and by["Tokyo"]["offset_label"] == "+13" and not by["Tokyo"]["awake"]
    assert by["Bangalore"]["offset_label"] == "+9½" and by["Newark"]["offset_label"] == "home"
    assert d["best"] == 5 and d["windows"] == [[7, 9], [10, 13]] and d["next_window"] == [10, 13]
    d2 = ck.fetch({"demo": 1, "at": "2026-09-07 11:00"})
    assert d2["open_now"] == [10, 13]
    # explicit zones, weekday shown only when the date differs
    d3 = ck.fetch({"zones": "Home=America/New_York,Auckland=Pacific/Auckland", "at": "2026-09-07 20:00"})
    assert d3["rows"][1]["weekday"] == "Tue" and d3["rows"][0]["weekday"] == ""


def test_sudoku_is_unique_and_deterministic():
    from glass.adapters import sudoku as sd
    a = sd.fetch({"date": "2026-09-07"})
    b = sd.fetch({"date": "2026-09-07"})
    assert a["grid"] == b["grid"] and a["code"] == b["code"] and a["clues"] == 32
    # clues agree with the solution, and the solution is valid
    assert all(v == 0 or v == s for v, s in zip(a["grid"], a["solution"]))
    sol = a["solution"]
    for k in range(9):
        assert sorted(sol[k * 9:(k + 1) * 9]) == list(range(1, 10))
        assert sorted(sol[k + 9 * j] for j in range(9)) == list(range(1, 10))
    # exactly one solution
    assert sd._solve(a["grid"][:], None, 2) == 1
    assert sd.fetch({"date": "2026-09-07", "difficulty": "hard"})["clues"] == 26
    assert sd.fetch({"date": "2026-09-08"})["grid"] != a["grid"]


def test_every_glyph_has_contrast():
    """Four grays only: text must sit two steps from what is under it (BLACK/DARK on WHITE, WHITE on BLACK/DARK).
    LIGHT text on white, or DARK on LIGHT, reads as nothing on e-ink."""
    import numpy as np
    from glass.canvas import Glass
    import glass
    weak = []
    orig = Glass.text
    cur = [""]

    def spy(self, xy, s, f, fill=0, anchor="la"):
        if str(s).strip():
            x0, y0, x1, y1 = [int(v) for v in self.d.textbbox(xy, str(s), font=f, anchor=anchor)]
            x0, y0, x1, y1 = max(0, x0), max(0, y0), min(self.w, x1), min(self.h, y1)
            if x1 > x0 and y1 > y0:
                reg = np.array(self.im.crop((x0, y0, x1, y1)))
                vals, counts = np.unique(reg, return_counts=True)
                bg, share = int(vals[counts.argmax()]), counts.max() / reg.size
                if share > 0.5 and abs(bg - fill) < 170:
                    weak.append((cur[0], f.size, fill, bg, str(s)[:16]))
        return orig(self, xy, s, f, fill, anchor)

    Glass.text = spy
    try:
        glass.load_all()
        for name in glass.REGISTRY:
            params = CASES[name][0] if name in CASES else {"demo": 1}
            for o in ("landscape", "portrait"):
                cur[0] = f"{name}/{o}"
                glass.render(name, params, o)
    finally:
        Glass.text = orig
    assert not weak, weak[:12]


def test_reading_pace_is_honest_about_gaps():
    from glass.adapters import reading as rd
    d = rd.fetch({"demo": 1, "date": "2026-09-07"})
    assert d["page"] == 246 and d["pages"] == 368 and d["left"] == 122
    assert len(d["per_day"]) == 14 and d["per_day"].count(0) == 3            # three days without an entry stay zero
    assert abs(d["pace"] - sum(d["per_day"]) / 14) < 1e-9 and d["finish"] == "Mon 14 Sep"
    assert d["finished_year"] == 4 and d["finished_pages"] == 1272
    # a book given inline, finished: no finish date, "finished" line
    done = rd.fetch({"book": '{"current":{"title":"X","author":"Y","pages":100,"started":"2026-09-01","log":[{"date":"2026-09-06","page":100}]}}', "date": "2026-09-07"})
    assert done["left"] == 0 and done["finish"] is None and done["source"] == "params"
    import glass
    glass.render("reading", {"book": '{"current":{"title":"An Extraordinarily Long Title That Keeps Going And Going","author":"A","pages":10,"started":"2026-09-01","log":[]}}', "date": "2026-09-07"}, "portrait")


def test_agenda_busy_vectors_and_free_window():
    from glass.adapters import agenda as ag
    d = ag.fetch({"demo": 1, "date": "2026-09-07"})
    assert len(d["days"]) == 7 and d["days"][0]["today"] and d["days"][0]["dow"] == "Mon"
    mon = d["days"][0]
    assert mon["busy"][11 - d["h0"]] == 60 and mon["busy"][10 - d["h0"]] == 0        # 11:00 review booked, 10:00 free
    assert 0 < mon["busy"][7 - d["h0"]] <= 30                                            # 07:30 run: half an hour
    assert mon["free"]["min"] == 90 and mon["free"]["start"] == "16:30" and mon["free"]["end"] == "18:00"  # clamped to 09–18
    assert all(0 <= v <= 60 for day in d["days"] for v in day["busy"])
    assert d["week_n"] == sum(x["n"] for x in d["days"])


def test_overnight_has_no_clock_and_looks_one_day_ahead():
    """The sleep frame is held for hours: it must not print the current time, and it speaks of tomorrow."""
    from glass.adapters import overnight as od
    d = od.fetch({"demo": 1, "date": "2026-09-07"})
    assert d["tomorrow_label"].startswith("Tuesday") and d["first"]["start"] == "07:30"
    assert all(x["due"] == "tomorrow" for x in d["due"]) and d["missing"] == []
    import glass.overnight as mod
    import inspect
    src = inspect.getsource(mod.render) + inspect.getsource(mod._footer) + inspect.getsource(mod._headline)
    assert "now" not in src.replace("Nothing", "").lower().replace("known", "")   # no clock on the sleep frame
    # a single dead source degrades alone and is named in the footer
    import requests
    real = requests.get

    def boom(*a, **k):
        raise requests.ConnectionError("offline")

    requests.get = boom
    try:
        d2 = od.fetch({"date": "2026-09-07"})
    finally:
        requests.get = real
    assert set(d2["missing"]) >= {"weather"} and d2["tomorrow_label"]


def test_todo_buckets_and_top():
    from glass.adapters import todo as td
    d = td.fetch({"demo": 1})
    assert d["top"]["text"].startswith("Flip") and d["n_open"] == 7 and d["n_done"] == 3 and d["n_today"] == 3
    assert [b["name"] for b in d["buckets"]] == ["today", "tomorrow", "week"]
    e = td.fetch({"items": '["a", {"text": "b", "done": true}, {"text": "c", "due": "someday"}]'})
    assert e["top"]["text"] == "a" and e["n_done"] == 1 and e["buckets"][-1]["name"] == "later"
    for o in ("landscape", "portrait"):
        assert glass.render("todo", {"items": "[]", "demo": 1}, o).size[0] > 0        # empty list renders
        assert glass.render("todo", {"items": json.dumps([{"text": f"item {i} " * 6, "due": "today"} for i in range(30)])}, o).size[0] > 0  # overflow clips


def test_arm_silhouette_stays_inside_its_box():
    """The folded arm reaches far to the left; in a narrow tile it must slide/shrink, not spill."""
    from glass.adapters import arm as ad_arm
    from glass.arm import draw_arm
    from glass.canvas import Glass
    d = ad_arm.fetch({"demo": 1})
    g = Glass((480, 800))
    x0, y0, w, h = 40, 100, 180, 160
    draw_arm(g, d, x0, y0, w, h, caption=False)
    im = g.snap()
    px = im.load()
    for y in range(0, 800):
        for x in list(range(0, x0 - 1)) + list(range(x0 + w + 2, 480)):
            assert px[x, y] == 255, f"ink outside the box at {x},{y}"


def test_focus_states_and_free_windows():
    from glass.adapters import focus as fd
    d = fd.fetch({"demo": 1, "clock": "11:20"})
    states = [b["state"] for b in d["blocks"]]
    assert states == ["done", "done", "done", "now", "planned", "planned", "planned"]
    assert d["current"]["left"] == 30 and d["done_min"] == 150 and d["planned_min"] == 350
    assert d["free"] and d["free"][0]["min"] >= d["free"][-1]["min"]          # biggest first
    assert all(w["s"] >= 11 * 60 + 20 for w in d["free"])                    # only ahead of now
    late = fd.fetch({"demo": 1, "clock": "15:40"})
    assert late["missed"] == 3 and late["current"] is None and late["next"]["start"] == "16:00"
    assert fd.free_windows([(60, 120), (100, 200), (400, 420)], 0, 480) == [
        {"start": "03:20", "end": "06:40", "min": 200, "s": 200, "e": 400},
        {"start": "00:00", "end": "01:00", "min": 60, "s": 0, "e": 60},
        {"start": "07:00", "end": "08:00", "min": 60, "s": 420, "e": 480}]
    for o in ("landscape", "portrait"):
        assert glass.render("focus", {"blocks": "[]", "demo": 1, "clock": "21:00"}, o).size[0] > 0


def test_offline_never_blanks_the_glass(monkeypatch):
    """Every network adapter degrades to its fixture with an 'offline · demo' stamp."""
    import requests

    def boom(*a, **k):
        raise requests.ConnectionError("offline")

    monkeypatch.setattr(requests, "get", boom)
    monkeypatch.setattr(requests.Session, "get", boom, raising=False)
    for name in glass.load_all():
        im = glass.render(name, {}, "landscape")
        assert im.size == (800, 480), name
        assert set(im.getcolors(8) and [c for _, c in im.getcolors(8)]) <= set(PALETTE), name
    # the stamp is present for an adapter that really raised
    d = glass.REGISTRY["weather_bar"].data({})
    assert d.get("_fallback") == "ConnectionError"
    # and absent when demo was asked for explicitly
    assert "_fallback" not in glass.REGISTRY["weather_bar"].data({"demo": 1})


def test_components_registry_and_preview_route():
    from fastapi.testclient import TestClient
    import server
    c = TestClient(server.app)
    r = c.get("/api/glass/components")
    assert r.status_code == 200
    names = [x["name"] for x in r.json()["components"]]
    assert "weather_bar" in names
    r = c.get("/api/glass/preview/weather_bar.png?demo=1&orientation=portrait")
    assert r.status_code == 200 and r.headers["content-type"] == "image/png"
    im = Image.open(__import__("io").BytesIO(r.content))
    assert im.size == (480, 800)
    assert c.get("/api/glass/preview/nope.png").status_code == 404


def test_show_publishes_exact_frame_and_sends_image_card(monkeypatch):
    from fastapi.testclient import TestClient
    import server
    import glass_routes
    sent = {}

    def fake_invoke(command, args, wait_s):
        sent.update({"command": command, "args": args})
        return {"ok": True, "mode": "fake", "result": "card_id=glass-weather_bar committed"}

    monkeypatch.setattr(server, "_do_invoke", fake_invoke)
    monkeypatch.setattr(server, "_device_name", lambda: "sticky")
    c = TestClient(server.app)
    r = c.post("/api/glass/show", json={"component": "weather_bar", "params": {"demo": 1},
                                        "orientation": "portrait", "force": True})
    assert r.status_code == 200, r.text
    body = r.json()
    assert sent["command"] == "render_ui"
    assert sent["args"]["type"] == "image"
    assert sent["args"]["url"].startswith("https://") and sent["args"]["url"].endswith("/0.raw")
    seq = body["frame"]["seq_id"]
    raw = c.get(f"/api/frames/{seq}/0.raw")
    assert raw.status_code == 200 and len(raw.content) == 96000  # the firmware's exact gray4 length
    assert int(raw.headers["content-length"]) == 96000
    # same pixels → same capability id (idempotent)
    r2 = c.post("/api/glass/show", json={"component": "weather_bar", "params": {"demo": 1},
                                         "orientation": "portrait", "force": True})
    assert r2.json()["frame"]["seq_id"] == seq
