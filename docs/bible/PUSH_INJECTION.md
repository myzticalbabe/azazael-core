# Push injection — how the canon reaches an agent that never opens it

A corpus nobody reads is a decoration. This file documents the one channel that
actually carries the canon into an agent's session, the three git hooks that keep
it current, and the honest limits of each.

```bash
python tools/bible_ship.py --render          # what the block says, change nothing
python tools/bible_ship.py --inject          # create/refresh it in the instruction files
python tools/bible_ship.py --check           # exit 1 when a block has gone stale
python tools/bible_ship.py --install-hooks .  # the three hooks + the plugin
python tools/bible_ship.py --verify .        # wired, current, and the plugin parses
python tools/bible_ship.py --self-test       # the negative controls
```

## Why a file, and not an MCP server

Measured in this tree: this client reads `mcpServers` but **discards the MCP
`initialize` instructions field**, so no server can push text into a session; and
the plugin API has no assistant-output hook, which is why the transcript gate can
only *report* a violation after emission. The channels that survive are the ones
the client reads as a file before the first turn — `AGENTS.md` first, then
`CLAUDE.md`, `.cursorrules`, `.github/copilot-instructions.md`.

So the block is generated from the corpus and written into those files. That is
the whole trick: **instruction propagation through the artefact the client
already reads**, which cannot be dropped, because dropping it is dropping the
file.

## What the block carries, and what it deliberately does not

| carries | why |
|---|---|
| every statute, with its `check` command | a law without its command is a proverb |
| the pinned configuration table (`docs/bible/bible.json` → `configs`) | the values that make the rules true, with the consequence of drifting |
| the decision-block contract | what must be printed before a state-changing action |
| the followup count | a countable obligation, so a miscount is visible |
| the claim rule | `done`/`fixed`/`passes` are worth what the command beside them is worth |

It does **not** restate the whole corpus. A block that duplicates the text is a
second source of truth, and the first thing that happens to a second source is
that it drifts. The corpus stays in `bible.json`; the block is a projection of
four fields and is regenerated, never edited.

## The three hooks

All three are marker-delimited (`# BIBLE-HOOK v1`). The installer **never
overwrites a hook it did not write** — an unmarked file is somebody's own gate and
is reported as `occupied`. Re-running the installer is idempotent.

| hook | what it does | why this hook |
|---|---|---|
| `pre-commit` | runs `bible_check.py --check`, then `bible_ship.py --check` | the corpus must not contradict itself, and the block must not describe an older corpus |
| `pre-push` | **push injection**: refreshes the block; if it had gone stale, aborts the push | pre-commit is skippable (a clone may never set `core.hooksPath`); push is the last moment before a stale block becomes *other people's* instruction file |
| `commit-msg` | a message that says done/fixed/passes must carry a `Verified:` line naming the command | a commit message is the one place an overclaim becomes permanent |

The push hook **aborts rather than injects-and-continues**. Injecting silently
during a push would ship a file no reviewer saw, and the next clone would receive
instructions that exist in no commit — the exact failure the corpus is about, in
the delivery mechanism.

Bypasses are declared and printed, never silent: `BIBLE_SKIP=1`,
`BIBLE_PUSH_BYPASS=1`, `BIBLE_CLAIM_BYPASS=1`.

## The plugin

`.opencode/plugin/bible.mjs` appends the block to the session system prompt on
`experimental.chat.system.transform`, reading it from disk each time — so
refreshing the canon needs no code change. It is shipped **verified or reported
unverified**: the installer runs `node --check` and says "syntax unchecked (node
not on PATH)" rather than claiming a pass it did not earn.

Two honest caveats:

- The plugin hook name (`experimental.chat.system.transform`) is the client's
  experimental API. If the client renames it, the plugin silently stops
  injecting — which is why the **file** channel is the primary one and the plugin
  is the amplifier, not the guarantee.
- If both channels fire, the block appears twice in the prompt. That is a waste of
  tokens, not a correctness bug, and it is cheaper than the alternative of
  having no channel that survives a client upgrade.

## In this repository specifically

| artefact | state |
|---|---|
| `.githooks/pre-commit` | **owned by this tree already**, so the installer reports `occupied` and leaves it alone; its own `[Bible]` stanza runs the corpus checker |
| `.githooks/pre-push` | planted by this kit (push injection) |
| `.githooks/commit-msg` | **not planted here**: `.githooks/prepare-commit-msg` already runs `tools/claim_guard.py` on the same message, and a second gate on the same claim is duplication, not defence |
| `AGENTS.md` | carries the injected block |
| `.opencode/plugin/bible.mjs` | planted, `node --check` verified |

The rule this table encodes: **add a gate where there is no gate, not where there
is already one.** Two gates on one claim produce the same verdict twice and a
false sense of depth.
