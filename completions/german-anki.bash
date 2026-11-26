# bash completion for german-anki. install.sh links this file into
# ~/.local/share/bash-completion/completions/, where bash-completion loads it on first use.
# Keep the lists below in step with german_anki/cli.py (tests/test_core.py checks this).

_german_anki() {
    local cur prev words cword
    _init_completion || return

    local commands="words add-row regen sentences add-sentence sync backfill-tags list-words setup-dict setup-anki doctor paths"
    local parts="definition examples conjugation pronunciation"

    # the subcommand is the first word that is not an option
    local cmd="" i
    for ((i = 1; i < cword; i++)); do
        if [[ ${words[i]} != -* ]]; then cmd=${words[i]}; break; fi
    done

    if [[ -z $cmd ]]; then
        COMPREPLY=($(compgen -W "$commands -h --help" -- "$cur"))
        return
    fi

    case $cmd in
        regen)
            case $prev in
                --only)
                    # comma-separated: complete the part after the last comma
                    local done_parts="" last="$cur"
                    if [[ $cur == *,* ]]; then done_parts="${cur%,*},"; last="${cur##*,}"; fi
                    compopt -o nospace
                    COMPREPLY=($(compgen -P "$done_parts" -W "$parts" -- "$last"))
                    return ;;
                --query | --hint | --min-words | --max-words) return ;;
            esac
            if [[ $cur == -* ]]; then
                COMPREPLY=($(compgen -W "--query --only --hint --min-words --max-words --dry-run -h --help" -- "$cur"))
                return
            fi
            # words of the deck (from the last `german-anki sync`). Matched by hand, not with
            # compgen -W, which chokes on quotes ("Wie geht's?"); they may contain spaces.
            local w prefix=${cur//\\/}
            prefix=${prefix#[\"\']}
            COMPREPLY=()
            while IFS= read -r w; do
                [[ $w == "$prefix"* ]] && COMPREPLY+=("$(printf '%q' "$w")")
            done < <(german-anki list-words 2>/dev/null)
            ;;
        sentences)
            if [[ $cur == -* ]]; then
                COMPREPLY=($(compgen -W "--skip-duplicates -h --help" -- "$cur"))
            else
                _filedir md
            fi
            ;;
        words | add-row)
            [[ $prev == --min-words || $prev == --max-words ]] && return
            COMPREPLY=($(compgen -W "--min-words --max-words -h --help" -- "$cur"))
            ;;
        setup-anki)
            COMPREPLY=($(compgen -W "-y --yes -h --help" -- "$cur"))
            ;;
        setup-dict)
            if [[ $prev == --zip ]]; then
                _filedir zip
            else
                COMPREPLY=($(compgen -W "--zip --force -h --help" -- "$cur"))
            fi
            ;;
        *)
            COMPREPLY=($(compgen -W "-h --help" -- "$cur"))
            ;;
    esac
}
complete -F _german_anki german-anki
