"""
📈 glass.adapters.markets — a handful of tickers, the way a paper prints them.

Source: params.symbols ("AAPL,MSFT,BTC-USD" or "Label=SYM,…") → ~/.tiny/sticky-markets.json
([{symbol, label?}] or ["AAPL", …]) → fixture list. Quotes: Yahoo's public chart endpoint
(no key; 3-month daily closes + 52-week range in one call per symbol, 8 s timeout). Any
failure → fixture quotes for that symbol, flagged demo, never a blank card. Output per
row: label, symbol, last, change, change_pct, closes[] (~63 d), lo52, hi52, pos52 0..1,
currency, stale (True when the fixture stood in). Header: how many are up/down, the
strongest and weakest of the day, and the quote time.
"""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any, Dict, List

import requests

FIXTURE = Path(__file__).resolve().parent.parent / "fixtures" / "markets.json"
USER_FILE = Path.home() / ".tiny" / "sticky-markets.json"
UA = {"User-Agent": "Mozilla/5.0 sticky-glass/1.0 (+https://github.com/cagataycali/sticky-the-reterminal)"}
URL = "https://query1.finance.yahoo.com/v8/finance/chart/{sym}?range=3mo&interval=1d"


def _want(params: Dict[str, Any], fixture: Dict[str, Any]) -> List[Dict[str, str]]:
    if params.get("symbols"):
        out = []
        for tok in str(params["symbols"]).split(","):
            tok = tok.strip()
            if tok:
                label, _, sym = tok.rpartition("=")
                out.append({"symbol": sym or tok, "label": label or (sym or tok)})
        return out
    if USER_FILE.exists() and not params.get("demo"):
        try:
            data = json.loads(USER_FILE.read_text())
            return [{"symbol": d, "label": d} if isinstance(d, str) else {"symbol": d["symbol"], "label": d.get("label", d["symbol"])} for d in data]
        except Exception:
            pass
    return [{"symbol": q["symbol"], "label": q["label"]} for q in fixture["rows"]]


def _quote(sym: str) -> Dict[str, Any]:
    r = requests.get(URL.format(sym=sym), headers=UA, timeout=8)
    r.raise_for_status()
    j = r.json()["chart"]["result"][0]
    m = j["meta"]
    closes = [round(float(c), 6) for c in j["indicators"]["quote"][0]["close"] if c is not None]
    last = round(float(m.get("regularMarketPrice") or closes[-1]), 6)
    # chartPreviousClose is the close BEFORE the 3-month window — not yesterday. Yesterday is
    # the last daily close that is not today's (the final bar is today's running candle).
    if closes and abs(closes[-1] - last) > 1e-4 * max(1.0, abs(last)):   # the API returns float32 noise
        closes.append(last)
    prev = float(m.get("previousClose") or (closes[-2] if len(closes) > 1 else last))
    lo, hi = float(m.get("fiftyTwoWeekLow") or min(closes)), float(m.get("fiftyTwoWeekHigh") or max(closes))
    return {"symbol": sym, "last": last, "change": last - prev, "change_pct": (last - prev) / prev * 100 if prev else 0.0,
            "closes": closes[-64:], "lo52": lo, "hi52": hi, "pos52": (last - lo) / (hi - lo) if hi > lo else 0.5,
            "currency": m.get("currency", ""), "name": m.get("shortName", sym), "stale": False,
            "ts": int(m.get("regularMarketTime") or 0)}


def fetch(params: Dict[str, Any]) -> Dict[str, Any]:
    fixture = json.loads(FIXTURE.read_text())
    want = _want(params, fixture)
    by_fix = {q["symbol"]: q for q in fixture["rows"]}
    rows, demo, ts = [], bool(params.get("demo")), 0
    for w in want[:6]:
        q = None
        if not demo:
            try:
                q = _quote(w["symbol"])
                ts = max(ts, q["ts"])
            except Exception:
                q = None
        if q is None:
            base = by_fix.get(w["symbol"]) or dict(by_fix[fixture["rows"][0]["symbol"]], symbol=w["symbol"], name=w["symbol"])
            q = dict(base, stale=not demo)   # a demo is not stale; a failed live pull is
            q["pos52"] = (q["last"] - q["lo52"]) / (q["hi52"] - q["lo52"]) if q["hi52"] > q["lo52"] else 0.5
        q["label"] = w["label"]
        rows.append(q)
    up = sum(1 for r in rows if r["change"] >= 0)
    best = max(rows, key=lambda r: r["change_pct"]) if rows else None
    worst = min(rows, key=lambda r: r["change_pct"]) if rows else None
    if params.get("at"):
        when = dt.datetime.fromisoformat(str(params["at"]))
    elif ts:
        when = dt.datetime.fromtimestamp(ts)
    else:
        when = dt.datetime.now()
    return {"rows": rows, "up": up, "down": len(rows) - up, "best": best, "worst": worst,
            "time": when.strftime("%H:%M"), "date_label": when.strftime("%a %-d %b"),
            "demo": demo or all(r["stale"] for r in rows), "any_stale": any(r["stale"] for r in rows)}
