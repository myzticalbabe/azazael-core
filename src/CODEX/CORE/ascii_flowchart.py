#!/usr/bin/env python3
"""
Azazael ASCII Flowchart Generator
==================================
Generates text-based flowcharts, tree diagrams, process flows,
and architecture diagrams from declarative YAML/dict descriptions.

Outputs: Unicode box-drawing characters, indented tree, or pure ASCII.
"""

# from typing import Dict, List, Tuple  # removed — all names have native equivalents
from dataclasses import dataclass, field
import argparse
from typing import List

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here


# --- Box-Drawing Characters -------------------------------------------------

BOX_CHARS = {
    "unicode": {
        "h": "-", "v": "|", "tl": "+", "tr": "+", "bl": "+", "br": "+",
        "cross": "+", "t_down": "+", "t_up": "+", "t_right": "+", "t_left": "+",
        "dh": "=", "dv": "|", "dtl": "+", "dtr": "+", "dbl": "+", "dbr": "+",
        "arrow": "->", "darrow": "=>", "bullet": "*", "diamond": "*",
    },
    "ascii": {
        "h": "-", "v": "|", "tl": "+", "tr": "+", "bl": "+", "br": "+",
        "cross": "+", "t_down": "+", "t_up": "+", "t_right": "+", "t_left": "+",
        "dh": "=", "dv": "|", "dtl": "+", "dtr": "+", "dbl": "+", "dbr": "+",
        "arrow": "->", "darrow": "=>", "bullet": "*", "diamond": "<>",
    },
}


@dataclass
class Node:
    """A node in the flowchart."""
    text: str
    children: list["Node"] = field(default_factory=list)
    shape: str = "box"  # box, diamond, circle, rounded, hexagon, plain
    style: str = ""      # bold, double, dashed, colored
    id: str = ""


class AsciiFlowchart:
    """Generate ASCII flowcharts from structured data."""

    def __init__(self, style: str = "unicode", width: int = 80):
        self.bc = BOX_CHARS.get(style, BOX_CHARS["unicode"])
        self.width = width
        self.indent = 2

    # --- Box Drawing ---------------------------------------------------------

    def _pad_text(self, text: str, width: int, align: str = "center") -> str:
        """Pad text to fit within a box of given width."""
        if len(text) > width - 2:
            text = text[:width - 5] + "..."
        if align == "center":
            return text.center(width - 2)
        elif align == "left":
            return " " + text.ljust(width - 3)
        else:
            return text.rjust(width - 3) + " "

    def draw_box(self, text: str, shape: str = "box", width: int = 40) -> list[str]:
        """Draw a single box with the given text and shape."""
        w = min(width, self.width - 4)
        lines = []

        if shape == "box":
            top = self.bc["tl"] + self.bc["h"] * (w - 2) + self.bc["tr"]
            mid = self.bc["v"] + self._pad_text(text, w) + self.bc["v"]
            bot = self.bc["bl"] + self.bc["h"] * (w - 2) + self.bc["br"]
            lines = [top, mid, bot]

        elif shape == "diamond":
            lines = [
                " " * (w // 2) + self.bc["diamond"],
                " " * max(0, (w - len(text)) // 2) + text,
                " " * (w // 2) + self.bc["diamond"],
            ]

        elif shape == "rounded":
            top = "╭" + "-" * (w - 2) + "╮"
            mid = "|" + self._pad_text(text, w) + "|"
            bot = "╰" + "-" * (w - 2) + "╯"
            lines = [top, mid, bot]

        elif shape == "double":
            top = self.bc["dtl"] + self.bc["dh"] * (w - 2) + self.bc["dtr"]
            mid = self.bc["dv"] + self._pad_text(text, w) + self.bc["dv"]
            bot = self.bc["dbl"] + self.bc["dh"] * (w - 2) + self.bc["dbr"]
            lines = [top, mid, bot]

        elif shape == "plain":
            lines = [text.center(w)]

        elif shape == "hexagon":
            indent_size = max(2, w // 6)
            lines = [
                " " * indent_size + "╱" + " " * (w - 2 * indent_size - 2) + "╲",
                "╱" + " " * (w - 2) + "╲",
                " " * indent_size + "╲" + " " * (w - 2 * indent_size - 2) + "╱",
            ]
            # Overlay text on middle line
            mid_idx = len(lines) // 2
            text_pos = (w - len(text)) // 2
            line = list(lines[mid_idx])
            for i, ch in enumerate(text):
                if text_pos + i < len(line):
                    line[text_pos + i] = ch
            lines[mid_idx] = "".join(line)

        else:
            # Default to box
            return self.draw_box(text, "box", width)

        return lines

    def connect_boxes(self, top_box: list[str], bottom_box: list[str],
                      centered: bool = True) -> list[str]:
        """Connect two boxes vertically."""
        top_width = len(top_box[0])
        bot_width = len(bottom_box[0])

        if centered:
            # Center-align the connector
            top_center = top_width // 2
            bot_center = bot_width // 2

            if top_center == bot_center:
                connector = " " * top_center + self.bc["v"]
            else:
                # Draw horizontal line then vertical
                start = min(top_center, bot_center)
                end = max(top_center, bot_center)
                h_line = self.bc["h"] * (end - start)
                if top_center < bot_center:
                    connector = " " * top_center + self.bc["bl"] + h_line + self.bc["v"]
                else:
                    connector = " " * bot_center + self.bc["v"] + h_line + self.bc["br"]
        else:
            connector = self.bc["v"].center(top_width)

        return [connector]

    # --- Flowchart Builders --------------------------------------------------

    def process_flow(self, steps: list[str], title: str = "",
                     shape: str = "box") -> str:
        """Generate a vertical process flow diagram."""
        box_width = min(50, max(len(s) + 6 for s in steps))
        lines = []

        if title:
            lines.append(title.center(box_width))
            lines.append(self.bc["dh"] * box_width)

        for i, step in enumerate(steps):
            box = self.draw_box(f"{i+1}. {step}", shape, box_width)
            for line in box:
                lines.append(line.center(self.width))
            if i < len(steps) - 1:
                arrow = " " * (self.width // 2) + self.bc["v"]
                lines.append(arrow)
                arrow_head = " " * (self.width // 2) + self.bc["arrow"]
                lines.append(arrow_head)

        return "\n".join(lines)

    def decision_tree(self, root: Node, prefix: str = "",
                      is_last: bool = True, depth: int = 0) -> list[str]:
        """Recursively render a decision tree."""
        lines = []

        if depth == 0:
            lines.append(f"{root.text}")
        else:
            connector = self.bc["bl"] if is_last else self.bc["t_right"]
            line_prefix = prefix + connector + self.bc["h"] * 2 + " "
            lines.append(f"{line_prefix}{root.text}")

        new_prefix = prefix + ("    " if is_last else self.bc["v"] + "   ")

        for i, child in enumerate(root.children):
            child_last = (i == len(root.children) - 1)
            lines.extend(self.decision_tree(child, new_prefix, child_last, depth + 1))

        return lines

    def decision_flowchart(self, root: Node) -> str:
        """Render a decision tree as a visual flowchart."""
        lines = self.decision_tree(root)
        return "\n".join(lines)

    def architecture_diagram(self, layers: dict[str, list[str]],
                             title: str = "System Architecture") -> str:
        """Generate a layered architecture diagram.

        layers: {"Layer Name": ["Component A", "Component B", ...], ...}
        """
        max_items = max(len(items) for items in layers.values())
        item_width = max(
            max((len(c) for c in components), default=10) + 4
            for components in layers.values()
        )
        total_width = item_width * max_items + (max_items - 1) * 3

        lines = []
        lines.append(title.center(total_width + 4))
        lines.append(self.bc["dh"] * (total_width + 4))
        lines.append("")

        layer_names = list(layers.keys())
        for layer_idx, (name, components) in enumerate(layers.items()):
            # Layer border
            border_top = self.bc["tl"] + self.bc["h"] * (total_width) + self.bc["tr"]
            border_bot = self.bc["bl"] + self.bc["h"] * (total_width) + self.bc["br"]

            lines.append(f"  {border_top}")
            lines.append(f"  {self.bc['v']} {name.center(total_width - 2)} {self.bc['v']}")

            # Component row
            comp_line = "  " + self.bc["v"] + " "
            for i in range(max_items):
                if i < len(components):
                    comp_line += components[i].center(item_width)
                else:
                    comp_line += " " * item_width
                if i < max_items - 1:
                    comp_line += " | "
            comp_line += " " + self.bc["v"]
            lines.append(comp_line)

            lines.append(f"  {border_bot}")

            # Arrow between layers
            if layer_idx < len(layer_names) - 1:
                arrows = "  " + " " * (total_width // 2) + self.bc["darrow"]
                lines.append(arrows)
                lines.append("  " + " " * (total_width // 2) + self.bc["dv"])
                lines.append("")

        return "\n".join(lines)

    def mind_map(self, root_text: str, branches: dict[str, list[str]],
                 width: int = 70) -> str:
        """Generate an indented mind map / tree structure."""
        lines = [f"* {root_text}", ""]

        for i, (branch, items) in enumerate(branches.items()):
            is_last_branch = (i == len(branches) - 1)
            branch_connector = self.bc["bl"] if is_last_branch else self.bc["t_right"]
            lines.append(f"{branch_connector}{self.bc['h'] * 2} * {branch}")

            prefix = "    " if is_last_branch else f"{self.bc['v']}   "
            for j, item in enumerate(items):
                is_last_item = (j == len(items) - 1)
                item_connector = self.bc["bl"] if is_last_item else self.bc["t_right"]
                lines.append(f"{prefix}{item_connector}{self.bc['h']} * {item}")

            lines.append(f"{prefix}" if is_last_branch else f"{self.bc['v']}")

        return "\n".join(lines)

    def pipeline_diagram(self, stages: list[tuple[str, str, str]],
                         title: str = "Pipeline") -> str:
        """Generate a horizontal pipeline diagram.

        stages: [("Stage Name", "Description", "status"), ...]
        status: "active", "done", "pending", "error"
        """
        stage_width = max(len(name) + 6 for name, _, _ in stages)

        lines = [title.center(len(stages) * (stage_width + 4)), ""]

        # Top borders
        top_line = ""
        for i, (name, _, _) in enumerate(stages):
            top_line += self.bc["tl"] + self.bc["h"] * stage_width + self.bc["tr"]
            if i < len(stages) - 1:
                top_line += "  "
        lines.append(top_line)

        # Stage names
        name_line = ""
        for i, (name, _, status) in enumerate(stages):
            name_line += self.bc["v"] + name.center(stage_width) + self.bc["v"]
            if i < len(stages) - 1:
                name_line += self.bc["h"] * 2
        lines.append(name_line)

        # Status indicators
        status_chars = {"active": "*", "done": "[OK]", "pending": "○", "error": "[X]"}
        status_line = ""
        for i, (_, desc, status) in enumerate(stages):
            indicator = status_chars.get(status, "?")
            status_line += self.bc["v"] + indicator.center(stage_width) + self.bc["v"]
            if i < len(stages) - 1:
                status_line += "  "
        lines.append(status_line)

        # Bottom borders
        bot_line = ""
        for i, (name, _, _) in enumerate(stages):
            bot_line += self.bc["bl"] + self.bc["h"] * stage_width + self.bc["br"]
            if i < len(stages) - 1:
                bot_line += "  "
        lines.append(bot_line)

        return "\n".join(lines)

    def comparison_table(self, headers: list[str], rows: list[list[str]],
                         title: str = "") -> str:
        """Generate an ASCII table with box-drawing borders."""
        col_widths = [len(h) for h in headers]
        for row in rows:
            for i, cell in enumerate(row):
                if i < len(col_widths):
                    col_widths[i] = max(col_widths[i], len(str(cell)))

        lines = []
        if title:
            lines.append(title)

        def draw_row(cells: list[str], left: str, mid: str, right: str) -> str:
            parts = [f"{cells[i].ljust(col_widths[i])}" for i in range(len(cells))]
            return left + f"{mid}".join(parts) + right

        # Header
        lines.append(draw_row(headers, self.bc["tl"], self.bc["t_down"], self.bc["tr"]))
        lines.append(draw_row(
            [self.bc["h"] * col_widths[i] for i in range(len(headers))],
            self.bc["t_right"], self.bc["cross"], self.bc["t_left"]
        ))

        # Data rows
        for row_idx, row in enumerate(rows):
            lines.append(draw_row(
                [str(row[i]) if i < len(row) else "" for i in range(len(headers))],
                self.bc["v"], self.bc["v"], self.bc["v"]
            ))
            if row_idx < len(rows) - 1:
                lines.append(draw_row(
                    [self.bc["h"] * col_widths[i] for i in range(len(headers))],
                    self.bc["t_right"], self.bc["cross"], self.bc["t_left"]
                ))

        # Footer
        lines.append(draw_row(
            [self.bc["h"] * col_widths[i] for i in range(len(headers))],
            self.bc["bl"], self.bc["t_up"], self.bc["br"]
        ))

        return "\n".join(lines)

    def worm_diagram(self, worm_name: str, body_segments: list[str],
                     head: str = "[-]", tail: str = ">>") -> str:
        """Draw a segmented worm diagram (for Azazael WORMS)."""
        segments_per_line = 8
        lines = [f"  {worm_name}", ""]

        # Head
        lines.append(f"  {head}")
        lines.append(f"  {self.bc['v']}")

        # Body segments
        for i in range(0, len(body_segments), segments_per_line):
            chunk = body_segments[i:i + segments_per_line]
            seg_line = "  " + self.bc["t_right"]
            for seg in chunk:
                seg_line += self.bc["h"] * 3 + f"[{seg}]" + self.bc["h"] * 3
            seg_line += self.bc["t_left"]
            lines.append(seg_line)

            if i + segments_per_line < len(body_segments):
                lines.append("  " + self.bc["v"])

        # Tail
        lines.append(f"  {self.bc['v']}")
        lines.append(f"  {tail}")

        return "\n".join(lines)


# --- Pre-built Azazael Diagrams ---------------------------------------------

def azazael_architecture() -> str:
    """Generate the full Azazael 7-tier architecture diagram."""
    fc = AsciiFlowchart(style="unicode")

    layers = {
        "5. DISTILLATION": ["Pattern Extraction", "Knowledge Synthesis", "Theory Engine"],
        "4. HYDRAMETA": ["ML Optimization", "Genetic Algorithm", "Parameter Tuning"],
        "3. METAWORMS": ["ERRORZ", "TRIMZ", "METAZ", "DEPENDZ", "VALIDZ", "HEALTHZ"],
        "2. CLUSTERWORMS": ["Parallel Ops", "Worm Collectives", "Resource Sharing"],
        "1. WORMS": ["SYSTEMZ", "PROCESSZ", "DISKZ", "NETWORXZ", "CLOCKZ", "MEMORZ"],
        "0. CONCURRENCY": ["Thread Manager", "Async Executor", "Lock Registry"],
        "-1. OS CONGRUENCY": ["Platform Abstraction", "Syscall Layer", "Driver Interface"],
    }

    return fc.architecture_diagram(layers, "AZAZAEL 7-TIER ARCHITECTURE")


def azazael_pipeline() -> str:
    """Generate the Azazael processing pipeline."""
    fc = AsciiFlowchart(style="unicode")

    stages = [
        ("DISCOVERY", "WORMZ crawls system", "active"),
        ("ANALYSIS", "METAWORMS processes", "done"),
        ("OPTIMIZE", "HYDRAMETA tunes", "pending"),
        ("VALIDATE", "TERENCE verifies", "pending"),
        ("PRESENT", "HORUS renders", "pending"),
    ]

    return fc.pipeline_diagram(stages, "AZAZAEL PROCESSING PIPELINE")


def azazael_worm_diagram() -> str:
    """Draw the SYSTEMZ worm anatomy."""
    fc = AsciiFlowchart(style="unicode")
    return fc.worm_diagram(
        "SYSTEMZ Worm Anatomy",
        ["CPU", "MEM", "DISK", "NET", "PROC", "THRD", "SOCK", "PIPE",
         "SIGNL", "TIMER", "EVENT", "QUEUE", "CACHE", "LOG", "CONFIG", "STATE"]
    )


# --- CLI ---------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
    description="Azazael ASCII Flowchart Generator",
    formatter_class=argparse.RawDescriptionHelpFormatter,
    epilog="""
Examples:
  ascii_flowchart.py --process "Step 1,Step 2,Step 3"
  ascii_flowchart.py --architecture
  ascii_flowchart.py --pipeline
  ascii_flowchart.py --worm SYSTEMZ "CPU,MEM,DISK,NET"
  ascii_flowchart.py --mindmap "Root" "Branch1: Item1,Item2" "Branch2: Item3"
  ascii_flowchart.py --table "Name,Age,Role" "Alice,30,Dev" "Bob,25,Ops"
  ascii_flowchart.py --style ascii
        """,
    )

    parser.add_argument("--process", help="Comma-separated process steps")
    parser.add_argument("--architecture", action="store_true", help="Show Azazael architecture")
    parser.add_argument("--pipeline", action="store_true", help="Show Azazael pipeline")
    parser.add_argument("--worm", nargs=2, metavar=("NAME", "SEGMENTS"),
                        help="Draw worm diagram with comma-separated segments")
    parser.add_argument("--mindmap", nargs="+", help="Mind map: ROOT BRANCH1:ITEM1,ITEM2 ...")
    parser.add_argument("--table", nargs="+", help="Table: HEADERS ROW1 ROW2 ...")
    parser.add_argument("--tree", nargs="+", help="Decision tree: ROOT CHILD1 CHILD2 ...")
    parser.add_argument("--style", choices=["unicode", "ascii"], default="unicode",
                        help="Box-drawing style")
    parser.add_argument("--width", type=int, default=80, help="Max output width")

    args = parser.parse_args()
    fc = AsciiFlowchart(style=args.style, width=args.width)

    if args.architecture:
        print(azazael_architecture())

    elif args.pipeline:
        print(azazael_pipeline())

    elif args.worm:
        name, segments_str = args.worm
        segments = [s.strip() for s in segments_str.split(",")]
        print(fc.worm_diagram(name, segments))

    elif args.process:
        steps = [s.strip() for s in args.process.split(",")]
        print(fc.process_flow(steps, "Process Flow"))

    elif args.mindmap:
        root = args.mindmap[0]
        branches = {}
        for branch_str in args.mindmap[1:]:
            if ":" in branch_str:
                name, items_str = branch_str.split(":", 1)
                branches[name.strip()] = [i.strip() for i in items_str.split(",")]
        print(fc.mind_map(root, branches))

    elif args.table:
        headers = [h.strip() for h in args.table[0].split(",")]
        rows = [[c.strip() for c in r.split(",")] for r in args.table[1:]]
        print(fc.comparison_table(headers, rows))

    elif args.tree:
        root = Node(args.tree[0])
        for child_text in args.tree[1:]:
            root.children.append(Node(child_text))
        print(fc.decision_flowchart(root))

    else:
        # Default: show everything
        print(azazael_architecture())
        print("\n" + "=" * 70 + "\n")
        print(azazael_pipeline())
        print("\n" + "=" * 70 + "\n")
        print(azazael_worm_diagram())


if __name__ == "__main__":
    main()
