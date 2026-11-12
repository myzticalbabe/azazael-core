"""Tab completion for azazael-ctl CLI.

Usage (bash):
    source <(python -m CORE.cli_completion)

Usage (zsh):
    eval "$(python -m CORE.cli_completion --zsh)"
"""


__all__ = [
    "FLAGS",
    "SUBSECTIONS",
    "print_completion",
]


SUBSECTIONS = {
    "crossref": ["summary", "backfill", "list", "unlinked", "query", "corr",
                 "export", "watch", "diff", "search", "interactive", "archive",
                 "dashboard"],
    "audit": ["status", "verify", "search", "diff", "repair", "archive",
              "sign", "rotate", "anchors", "duplicates", "stats", "export",
              "dashboard"],
    "memory": ["status", "recent", "stats", "search", "timeline", "gc",
               "purge", "merge", "duration", "tags", "distribution", "orphans",
               "dashboard"],
    "inference": ["benchmark", "models", "classify", "stats", "health"],
}

_MAIN_COMMANDS = list(SUBSECTIONS) + ["status", "backup", "help",
                                              "--json", "--version"]

FLAGS = ["--help", "--json", "--verbose", "--output", "--limit", "--format",
         "--model", "--iterations", "--max-tokens", "--temperature"]


def _bash_completion():
    """Generate bash completion script with inline SUBSECTIONS array."""
    # Build the declare -A block for SUBSECTIONS
    subs_declares = []
    for section, cmds in SUBSECTIONS.items():
        quoted = " ".join(cmds)
        subs_declares.append(f"    [{section}]=\"{quoted}\"")

    return f"""_azazael_ctl() {{
    local cur prev words cword
    _init_completion || return

    if [[ $cword -eq 1 ]]; then
        COMPREPLY=($(compgen -W "{' '.join(_MAIN_COMMANDS)}" -- "$cur"))
        return
    fi

    local section="${{words[1]}}"
    # Hardcoded subsection commands for each section
    case $section in
        crossref) local cmds="{' '.join(SUBSECTIONS['crossref'])}" ;;
        audit)   local cmds="{' '.join(SUBSECTIONS['audit'])}" ;;
        memory)  local cmds="{' '.join(SUBSECTIONS['memory'])}" ;;
        inference) local cmds="{' '.join(SUBSECTIONS['inference'])}" ;;
        *)       COMPREPLY=($(compgen -W "{' '.join(FLAGS)}" -- "$cur")); return ;;
    esac

    if [[ $cword -eq 2 ]]; then
        COMPREPLY=($(compgen -W "$cmds" -- "$cur"))
    else
        COMPREPLY=($(compgen -W "{' '.join(FLAGS)}" -- "$cur"))
    fi
}} &&
complete -F _azazael_ctl azazael-ctl
"""


def _zsh_completion():
    """Generate zsh completion script."""
    lines = ["#compdef azazael-ctl", "", "_azazael_ctl() {",
             "  local -A SUBSECTIONS"]
    for section, cmds in SUBSECTIONS.items():
        lines.append(f"    SUBSECTIONS[{section}]=\"{' '.join(cmds)}\"")
    lines += [
        f"  local -a MAIN_COMMANDS=({' '.join(_MAIN_COMMANDS)})",
        "  local -a FLAGS=({' '.join(FLAGS)})",
        "", "  _arguments \\",
        "    '1:command:->cmds' \\",
        "    '*:args:->args'",
        "", "  case $state in",
        "    cmds) _describe 'command' MAIN_COMMANDS ;;",
        "    args) _describe 'args' FLAGS ;;",
        "  esac",
        "}",
        "",
        "_azazael_ctl \"$@\"",
    ]
    return "\n".join(lines)


def print_completion(shell="bash"):
    """Print completion script for the given shell."""
    if shell == "zsh":
        print(_zsh_completion())
    else:
        print(_bash_completion())


if __name__ == "__main__":
    import sys
    shell = "zsh" if "--zsh" in sys.argv else "bash"
    print_completion(shell)
