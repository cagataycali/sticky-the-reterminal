"""Source (2): prompt → agent turn → anim-grammar JSON → frames.

"show me a spinning duck" — a Bedrock converse turn is taught the EXACT
grammar frames.render_sequence validates, emits a spec, and the validator
is the judge: a SpecError is fed back verbatim for ONE repair attempt
(the same honesty loop a human gets from the 422 body). No new grammar is
invented here — the model writes what /api/frames already accepts, so
every prompt-born sequence replays through the same store, manifest,
ANSI twin and tiny_stream_play path as a hand-written one.

Model: haiku-class (fast, cheap — a spec is ~1-2KB of JSON, not prose).
Creds: ambient AWS (same account the tiny daemons on this Mac use).
"""
from __future__ import annotations

import json
import os
import re
from typing import Any, Dict

import frames as sticky_frames

MODEL_ID = os.getenv("STICKY_FRAMES_MODEL",
                     "us.anthropic.claude-haiku-4-5-20251001-v1:0")
REGION = os.getenv("STICKY_FRAMES_REGION", "us-east-1")

SYSTEM = """You compose e-ink animations for an 800x480 1-bit panel (~2fps).
Reply with ONE JSON object, no prose, no code fences. Schema:
{"anim_id":"<slug>","version":1,"mode":"1bit_partial","frame_budget_ms":500,
 "loop":1|-1,"background":"white"|"black","frames":[<ops>]}
Ops (each PAINT op emits one frame; canvas persists between ops):
 {"op":"scene","clear":"white","shapes":[{"kind":"rect"|"ellipse",
   "tile":{"x":..,"y":..,"w":..,"h":..},"color":"black"|"dark"|"light"|"white"}]}
   — compose a whole pose as ONE frame (USE THIS for multi-shape poses)
 {"op":"rect","tile":{...},"color":...}   — single solid rectangle
 {"op":"ellipse","tile":{...},"color":...} — solid ellipse inscribed in tile
 {"op":"blit","tile":{...},"glyph":"text","color":"black"|"white"}
   — centered text/emoji in tile (tile erased first)
 {"op":"delay","ms":N} — hold the last frame
Rules: integer coords, 0<=x, x+w<=800, 0<=y, y+h<=480; total frames <= 24
(cap 120 but stay small); frame_budget_ms >= 250 (500 is right); motion =
repeat scene ops with shifted tiles; e-ink has no smooth motion — design
poses that read as change, like a flipbook. loop:-1 means the device
replays the sequence forever (good for idle loops)."""


def _extract_json(text: str) -> Dict[str, Any]:
    text = text.strip()
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        raise sticky_frames.SpecError(
            f"model reply contained no JSON object: {text[:120]}")
    return json.loads(m.group(0))


def prompt_to_frames(prompt: str, max_attempts: int = 2) -> Dict[str, Any]:
    """One (repairable) agent turn → validated, rendered, stored sequence."""
    import boto3
    client = boto3.client("bedrock-runtime", region_name=REGION)
    messages = [{"role": "user", "content": [{"text": prompt}]}]
    last_err = ""
    for attempt in range(max_attempts):
        r = client.converse(modelId=MODEL_ID, messages=messages,
                            system=[{"text": SYSTEM}],
                            inferenceConfig={"maxTokens": 4000,
                                             "temperature": 0.4})
        reply = r["output"]["message"]["content"][0]["text"]
        try:
            spec = _extract_json(reply)
            manifest = sticky_frames.render_sequence(spec)
            manifest = dict(manifest)
            manifest["source"] = "prompt"
            manifest["prompt"] = prompt[:300]
            manifest["attempts"] = attempt + 1
            return manifest
        except (sticky_frames.SpecError, ValueError) as e:
            last_err = str(e)
            # feed the validator's sentence back — one honest repair turn
            messages.append({"role": "assistant", "content": [{"text": reply}]})
            messages.append({"role": "user", "content": [{
                "text": f"Your spec was refused by the validator: {last_err}. "
                        "Reply with the corrected full JSON object only."}]})
    raise sticky_frames.SpecError(
        f"agent could not produce a valid spec in {max_attempts} attempts — "
        f"last refusal: {last_err}")
