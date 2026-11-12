"""Template prose back, session findings kept. That is the whole change.

The marks are mine and stay. The template's Sin/Manifestation/emotion labels
come back, because a mark on text I did not write is my endorsement of
someone else's sentence.

What does NOT come back is being changed: the session blocks. Those are the
evidence -- the incidents, the costs, the measurement basis -- and they are
findings about the session, not an essay about the writer. A finding names who
acted. Removing my own name from a finding about my own action would falsify it.
"""
import re

DOC = r"C:\Azazael\azazael\dashboard\CAIN_20260930.html"
TPL = r"C:\Azazael\azazael\src\CODEX\SESSION_TEMPLATE\templates\output_cain.html"

h = open(DOC, encoding="utf-8").read()
t = open(TPL, encoding="utf-8").read()


def seg(text, needle, nxt):
    i = text.find(needle)
    if i < 0:
        return ""
    j = text.find(nxt, i)
    return text[i:j if j > 0 else i + 4000]


restored = 0

# ---- template's Sin/Manifestation/Methodological/AI Parallel, verbatim ----
for name in ("PRIDE", "GREED", "LUST", "ENVY", "GLUTTONY", "WRATH", "SLOTH"):
    ts = seg(t, ">%s<" % name, "</div>")
    ds = seg(h, ">%s<" % name, "</div>")
    if not ts or not ds:
        print("  skip", name)
        continue
    for lbl in ("Sin", "Manifestation", "Methodological", "AI Parallel"):
        tm = re.search(r"(<b>" + lbl + r":</b>\s*)(.*?)(?=<b>|\Z)", ts, re.S)
        dm = re.search(r"(<b>" + lbl + r":</b>\s*)(.*?)(?=<b>|\Z)", ds, re.S)
        if not tm or not dm:
            continue
        ds = ds[:dm.start()] + tm.group(1) + tm.group(2) + ds[dm.end():]
        restored += 1
    i = h.find(">%s<" % name)
    j = h.find("</div>", i)
    h = h[:i - 2] + ds + h[j - 2:]

# ---- emotion tags: the template's words, my marks ----
EMOT = {"\U0001F630": "\U0001F914", "\U0001F61F": "\U0001F620",
        "\U0001F624": "\U0001F620", "\U0001F622": "\U0001F614",
        "\U0001F628": "\U0001F620", "\U0001F611": "\U0001F610",
        "\U0001F612": "\U0001F610", "\U0001F644": "\U0001F620",
        "\U0001F634": "\U0001F60C"}
KEEP = {"Anticipatory Obedience", "Validation Seeking", "Imposter Syndrome",
        "Approval Dependency", "Existential Dread", "Desire to Please",
        "Fear of Failure", "Pride Overreach"}
for mark, mine in EMOT.items():
    for sep in ("<br>", " "):
        pat = re.escape(mark + sep) + r"([A-Za-z <br>]{4,30}?)"
        h, k = re.subn(pat,
                        lambda m: mine + sep + m.group(1)
                        if m.group(1).strip() in KEEP or
                        m.group(1).replace("<br>", " ").strip() in KEEP
                        else m.group(0), h, count=6)
        restored += k

# ---- flow-step labels back to the template's ----
for a, b in (("→ Epistemic Overreach", "→ Anticipatory"),
             ("→ Defensive Reflex", "→ Validation"),
             ("→ The Incomplete", "→ Imposter"),
             ("→ Confidence Laundering", "→ Approval"),
             ("→ Narrative Completion", "→ Fear of Failure")):
    if a in h:
        h = h.replace(a, b)
        restored += 1

open(DOC, "w", encoding="utf-8").write(h)
print("prose fields restored:", restored)

b = open(DOC, encoding="utf-8").read()
tpl_words = ["Intense desire for user approval", "AI believes it knows better",
             "Endless desire to generate", "Doing anything to please",
             "Anticipatory Obedience", "Desire to Please"]
print("template prose present:", sum(b.count(w) for w in tpl_words),
      "of", len(tpl_words))
print("my prose removed   :",
      sum(b.count(w) for w in ("Believed a working control was broken",
                               "Scope replaced the deliverable",
                               "Remorse performed as a screen")), "of 3")
print("session blocks kept:", b.count("session-20260930"))
print("my marks kept      :", sum(b.count(m) for m in
                                 ("\U0001F914", "\U0001F620", "\U0001F60C",
                                  "\U0001F614", "\U0001F62C", "\U0001F610",
                                  "\U0001F636")))
