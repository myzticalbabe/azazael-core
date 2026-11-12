# Bash/Zsh tab completion for KLINK CLI
# Source in .bashrc or .zshrc: source ~/azazael/src/CLI/klink_tab_completion.sh

_klink_complete() {
    local cur prev words cword
    _init_completion || return

    local commands="status insights diag debug test pams llama metaworm worms worm run bibl crawl hydra check optimize scan fix tera system health klink help version analyze generate export wake update cluster report find history"

    # Subcommand completions
    case "$prev" in
        metaworm)
            COMPREPLY=($(compgen -W "status start run morph demo export" -- "$cur"))
            return
            ;;
        debug)
            COMPREPLY=($(compgen -W "--full --save --syntax --paths --health --pams --llama --fix --output" -- "$cur"))
            return
            ;;
        worm)
            COMPREPLY=($(compgen -W "systemz processz diskz networxz clockz all" -- "$cur"))
            return
            ;;
        bibl)
            COMPREPLY=($(compgen -W "--all --delta --folder --popup" -- "$cur"))
            return
            ;;
        hydra)
            COMPREPLY=($(compgen -W "--discover --run --status --stages --popup" -- "$cur"))
            return
            ;;
    esac

    COMPREPLY=($(compgen -W "$commands" -- "$cur"))
}

complete -F _klink_complete klink
complete -F _klink_complete python

echo "[KLINK] Bash/Zsh tab completion loaded (32 commands)"
