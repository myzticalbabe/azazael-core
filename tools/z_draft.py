#!/usr/bin/env python
"""Draft S2/S5 glass prompts via local Z-engineer (lms :1234), save raw drafts."""
import json
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
MODEL = "qwen3-4b-z-image-engineer-v4"
SYS = "You write image prompts for a FLUX image model. Reply with ONLY the prompt paragraph."

BRIEFS = {
    "S2": (
        "Write a single-paragraph rich ornate image prompt (150-180 words, ASCII only, "
        "natural language, no weight syntax) for a stained-glass window panel showing a "
        "garden promenade: a spade king with short silver-grey hair, black spade crown and "
        "black-silver armor walks beside a diamond queen with very long red-orange hair and "
        "diamond tiara in a white gown, both her arms at her sides with defined hands, dense "
        "roses plus blue delphiniums, bluebirds, sunset sky, every element cut colored glass "
        "with black lead came."
    ),
    "S5": (
        "Write a single-paragraph rich ornate image prompt (150-180 words, ASCII only, "
        "natural language, no weight syntax) for a stained-glass window panel showing a royal "
        "twin-throne finale: a spade king with short silver-grey hair and black spade crown "
        "on one throne, a diamond queen with very long red-orange hair and diamond tiara on "
        "the other, both arms of each resting on chair arms with defined hands, grand throne "
        "hall, arched window behind them filled with a glowing crown-and-roses narrative in "
        "colored glass that is opaque with no transparency, floral glass border, every element "
        "cut colored glass with black lead came."
    ),
}


def complete(user_text):
    body = json.dumps(
        {
            "model": MODEL,
            "messages": [
                {"role": "system", "content": SYS},
                {"role": "user", "content": user_text},
            ],
            "temperature": 0.7,
            "max_tokens": 600,
        }
    ).encode()
    req = urllib.request.Request(
        "http://127.0.0.1:1234/v1/chat/completions",
        data=body,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=600) as r:
        return json.loads(r.read().decode())["choices"][0]["message"]["content"].strip()


def main():
    for key, brief in BRIEFS.items():
        text = complete(brief)
        dest = HERE / "style_samples" / f"ZRAW_{key}_draft.txt"
        dest.write_text(text, encoding="utf-8")
        print(f"=== {key} ({len(text.split())} words) -> {dest.name} ===")
        print(text)
        print()


if __name__ == "__main__":
    main()
