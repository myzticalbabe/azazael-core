# GephiHeadless — deterministic headless Gephi rendering

Runs Gephi 0.11.x **without a GUI or window focus**: imports a GraphML file
through Gephi's own pipeline, runs a layout (ForceAtlas2 by default, or any
layout the install registers), optionally colors/labels nodes from columns,
exports post-layout node attributes to CSV, and renders a PNG via Gephi's
preview/export pipeline. Fully scriptable and reproducible.

Two entry points live in this directory:

| File | Role |
|---|---|
| `GephiHeadless.java` | The engine itself — a plain `main()` CLI, single file, no build system |
| `run_headless.py` | The launcher — finds Gephi + a JDK, collects ~1,600 jars into a flat-classpath `@argfile`, (re)compiles `GephiHeadless.java` when stale, then runs the engine with the args you pass |

> **Why an `@argfile`?** A real Gephi install ships ~1,600 jars. That classpath
> exceeds the OS command-line length limit, so `run_headless.py` writes it to
> `_cp.args` / `_compile.args` (Java `@argfile` syntax) and the engine is
> launched with `java @_cp.args GephiHeadless …`. The files are removed after
> each run unless `--keep` is given.

## Prerequisites

- A Gephi 0.11.x install (default lookup: `%LOCALAPPDATA%\Programs\Gephi-0.11.2`
  then `%PROGRAMFILES%\Gephi-0.11.2`), and
- A JDK 17 (default lookup: `C:\Program Files\Eclipse Adoptium\jdk-17*`).
  A JRE is **not** enough — `javac` must be available to compile the driver.

## Quick start

```bash
python run_headless.py                                # FA2 render of the bcc graph
python run_headless.py --list-layouts                 # list every registered layout
python run_headless.py --input g.graphml --output g.png --iterations 200
```

## run_headless.py flags (launcher)

| Flag | Default | Meaning |
|---|---|---|
| `--gephi DIR` | auto-detected | Path to the Gephi install (contains `gephi/modules/…`) |
| `--input FILE` | sibling `bcc_network.graphml` | GraphML to import |
| `--output FILE` | `bcc_network_layout.png` here | PNG to write |
| `--layout NAME` | `forceatlas2` | Layout to run (see the engine table below) |
| `--iterations N` | engine default `300` | Layout iterations |
| `--attributes FILE` | off | Also export node attributes (incl. post-layout `X`,`Y`,`Degree`) to CSV |
| `--width W` | `2000` | Output PNG width in px |
| `--height H` | `1400` | Output PNG height in px |
| `--margin M` | `60` | PNG margin in px |
| `--no-labels` | off | Do not render node labels |
| `--color-by COL` | off | Color nodes by a column (e.g. `Community`) |
| `--label-by COL` | off | Use a column's value as each node's display label (GraphML stores names in attributes, not element labels) |
| `--label-size N` | default (proportional) | Explicit label font size — also disables per-node proportional scaling |
| `--list-layouts` | off | Print available layouts and exit |
| `--no-compile` | off | Skip the up-to-date check / compile step |
| `--keep` | off | Keep the generated `_cp.args` / `_compile.args` after the run |

Every launcher flag is forwarded verbatim to the engine, so the engine flag
table below describes the same behavior in more detail.

## Engine flags (GephiHeadless.java)

```
java GephiHeadless --input in.graphml --output out.png
                   [--layout NAME] [--iterations N] [--attributes out.csv]
                   [--width W] [--height H] [--margin M] [--no-labels]
                   [--color-by COL] [--label-by COL] [--label-size N]
                   [--list-layouts]
```

| Flag | Default | Meaning |
|---|---|---|
| `--input FILE` | — (required) | GraphML to import |
| `--output FILE` | — (required) | PNG to write |
| `--layout NAME` | `forceatlas2` | Layout by alias, class name, or name prefix. `forceatlas2` / `fa2` / `fruchterman` / `yifanhu` / `openord` all resolve. Unknown names print the full list and exit 2. |
| `--iterations N` / `--iters N` | `300` | Number of layout steps |
| `--attributes FILE` | off | Write `Id,Label,X,Y,Degree` + all node columns to CSV (post-layout X/Y — feed this to the SNA pipeline) |
| `--width W` | `2000` | PNG width in px |
| `--height H` | `1400` | PNG height in px |
| `--margin M` | `60` | PNG margin in px |
| `--no-labels` | labels on | Suppress node labels |
| `--color-by COL` | off | Deterministic categorical colors per distinct column value (sorted; palette is colorblind-friendly). For `Community` columns the first five groups map to distinct hues. |
| `--label-by COL` | off | Promote a column to the display label. Needed because GraphML element labels are ids; the name lives in a `Label`/`d1` attribute. |
| `--label-size N` | proportional | Explicit label font size; disables `NODE_LABEL_PROPORTIONAL_SIZE` so labels are legible at high node counts |
| `--list-layouts` | off | Print every `LayoutBuilder` the install registers, then exit 0 |

Positional legacy form is still accepted:

```
java GephiHeadless in.graphml out.png [iterations]
```

## Example commands

Render with the default ForceAtlas2 layout:

```bash
python run_headless.py --input net.graphml --output net.png
```

Small graphs converge faster; medium graphs look better with more passes:

```bash
python run_headless.py --input net.graphml --output net.png --iterations 500
```

Use a different layout (any registered builder by name):

```bash
python run_headless.py --input net.graphml --output net.png --layout fruchterman
python run_headless.py --input net.graphml --output net.png --layout yifanhu --iterations 200
python run_headless.py --list-layouts          # see every layout this Gephi ships
```

Export post-layout coordinates + degree for analysis (SNA cross-checks,
k-core-vs-layout comparisons):

```bash
python run_headless.py --input net.graphml --output net.png \
    --attributes net_fa2_coords.csv
```

Community-colored render (colors by the `Community` column):

```bash
python run_headless.py --input net.graphml --output net_comm.png \
    --color-by Community
```

Labels from the name attribute, fixed legible size, larger canvas:

```bash
python run_headless.py --input net.graphml --output net_names.png \
    --label-by Label --label-size 14 --width 2400 --height 1600
```

Huge graphs (tens of thousands of nodes): drop labels and shrink the canvas
spread with extra room, or they become an unreadable wall:

```bash
python run_headless.py --input big.graphml --output big.png \
    --no-labels --color-by Community --iterations 300
```

Direct engine invocation (bypasses the launcher — e.g. to raise the heap for
very large graphs; note the engine must be compiled first and the argfile must
exist):

```bash
javac @_compile.args
java -Xmx4g @_cp.args GephiHeadless --input big.graphml --output big.png \
    --layout forceatlas2 --iterations 300 --no-labels --color-by Community
```

## What a run prints

The engine logs numbered milestones — `[1]` project/workspace, `[2]` import
(with node/edge counts), `[3]` graph summary, `[4]` layout executed, `[4c]`
color groups applied, `[4e]` labels set, `[4b]` attributes export size,
`[5]` PNG export, then `DONE`. Watch `[3]` for import surprises and `[4]` to
confirm the requested layout actually ran.

## Provenance

Built 2026-09-04 for the BCC investigation (Sep-4(57)), extended to a reusable
CLI Sep-4(60) with community coloring + real-name labels Sep-4(61) and
post-layout attribute export (k-core-vs-layout cross-check) Sep-4(62).
Verified against a Gephi 0.11.2 install with a Temurin JDK 17. Mirrored
hash-MATCH across the azazael repo, azazael-dev, and the G: archives. Nothing
sent; BCC excluded from all recipient lists.
