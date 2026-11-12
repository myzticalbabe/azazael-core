"""Eve for women in S.T.E.M.: learning tracks plus real doors.

Smallest native slice: three learning-style tracks (guided, visual,
hands-on lab) that reuse Eve's own tools, and referrals to established
women-in-STEM organizations. No invented mentors, no invented
scholarships -- every referral is a real org with its real URL.
"""
TRACKS = {
    "guided": [
        "STEP 1 -- NAME THE GOAL: one sentence (e.g. ship a Python CLI).",
        "STEP 2 -- FIRST PROOF: eve infer on a tiny local model.",
        "STEP 3 -- MAKE IT REAL: eve morph convert a CSV you care about.",
        "STEP 4 -- BREAK IT SAFELY: eve morph mutate, then re-verify.",
        "STEP 5 -- SHOW IT: eve morph report into Markdown you can share.",
    ],
    "visual": [
        "STEP 1 -- SEE THE SYSTEM: eve ablate, read the provider rows.",
        "STEP 2 -- RENDER IT: eve wasm --from <ablate log> --out <dir>.",
        "STEP 3 -- OPEN index.html: click each step, watch verdict colors.",
        "STEP 4 -- DECIDE VISUALLY: use the decision blocks on the page.",
    ],
    "lab": [
        "STEP 1 -- SET UP THE BENCH: a game dir + a mod dir (mod.json).",
        "STEP 2 -- APPLY: eve modgame --game G --mod M, check the diff.",
        "STEP 3 -- EVOLVE: eve modgame --evolve --fitness '{...}'.",
        "STEP 4 -- KEEP THE BEST: read .eveevo/best.json, apply-best.",
        "STEP 5 -- REVERT CLEAN: eve modgame --game G --revert.",
    ],
}

RESOURCES = [
    ("Girls Who Code", "https://girlswhocode.com/"),
    ("Society of Women Engineers", "https://swe.org/"),
    ("NCWIT", "https://ncwit.org/"),
    ("Women Who Code", "https://womenwhocode.com/"),
    ("AnitaB.org", "https://anitab.org/"),
]


def list_tracks():
    """Track names Eve can teach natively."""
    return sorted(TRACKS)


def plan_track(style, goal):
    """Steps for a style toward a goal. Raises ValueError on unknown style."""
    style = (style or "").lower()
    if style not in TRACKS:
        raise ValueError("unknown track %r (try: %s)"
                         % (style, ", ".join(list_tracks())))
    goal = (goal or "my first STEM build").strip()[:120]
    return ["GOAL: %s" % goal] + list(TRACKS[style])


def referrals():
    """Real women-in-STEM organizations (name, url)."""
    return list(RESOURCES)
