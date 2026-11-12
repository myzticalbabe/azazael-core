"""
NER Visualization — Color-highlighted entities using Stanza
"""

try:
    import stanza
except ImportError as _dep_exc:
    raise SystemExit("visualize_ner.py needs stanza (pip install stanza; downloads models on first use): " + str(_dep_exc)) from _dep_exc

TEXT = "Apple CEO Tim Cook announced that the new iPhone 16 will launch \
in San Francisco on September 12, 2025. Microsoft acquired Activision \
for $68.7 billion. Tesla's Elon Musk visited the European Central Bank \
in Frankfurt, Germany."

# Colors by entity type
COLORS = {
    "PERSON": "\033[38;5;196m",   # Red
    "ORG": "\033[38;5;27m",       # Blue
    "GPE": "\033[38;5;82m",       # Green
    "PRODUCT": "\033[38;5;200m",  # Magenta
    "DATE": "\033[38;5;226m",     # Yellow
    "MONEY": "\033[38;5;208m",    # Orange
    "PERCENT": "\033[38;5;51m",   # Cyan
    "LOC": "\033[38;5;82m",       # Green
    "MISC": "\033[38;5;200m",     # Magenta
    "PER": "\033[38;5;196m",      # Red
    "DEFAULT": "\033[38;5;245m",  # Gray
}
RESET = "\033[0m"
BOLD = "\033[1m"

print("=" * 65)
print("  NER VISUALIZATION")
print("=" * 65)
print(f"\n{BOLD}Input:{RESET}\n  {TEXT}\n")

nlp = stanza.Pipeline("en", processors="tokenize,ner", verbose=False, use_gpu=False)
doc = nlp(TEXT)

print(f"{BOLD}Entities:{RESET}\n")
for sent in doc.sentences:
    # Build colored text with entity annotations
    result = ""
    tokens = sent.tokens
    for token in tokens:
        text = token.text
        if token.ner:
            ent_type = token.ner.split("-")[-1]  # B-PERSON → PERSON
            color = COLORS.get(ent_type, COLORS["DEFAULT"])
            if token.ner.startswith("B-"):
                result += f"{color}{BOLD}{text}{RESET} "
            else:
                result += f"{color}{text}{RESET} "
        else:
            result += f"{text} "
    print(f"  {result}\n")

# Summary
print(f"\n{BOLD}Entity Summary:{RESET}\n")
ents = {}
for sent in doc.sentences:
    for ent in sent.ents:
        ents.setdefault(ent.type, []).append(ent.text)

for etype, values in sorted(ents.items()):
    color = COLORS.get(etype, COLORS["DEFAULT"])
    print(f"  {color}{BOLD}{etype:8}{RESET} : {', '.join(set(values))}")
print()
