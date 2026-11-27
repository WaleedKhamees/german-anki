# bash completion for danki. install.sh links this file into
# ~/.local/share/bash-completion/completions/, where bash-completion loads it on first use.
# Keep the lists below in step with danki/cli.py (tests/test_core.py checks this).

_danki() {
    local cur prev words cword
    _init_completion || return

    local commands="words add-row serve regen sentences add-sentence subs sync backfill-tags list-words setup-dict setup-anki doctor paths"
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
            # words of the deck (from the last `danki sync`). Matched by hand, not with
            # compgen -W, which chokes on quotes ("Wie geht's?"); they may contain spaces.
            local w prefix=${cur//\\/}
            prefix=${prefix#[\"\']}
            COMPREPLY=()
            while IFS= read -r w; do
                [[ $w == "$prefix"* ]] && COMPREPLY+=("$(printf '%q' "$w")")
            done < <(danki list-words 2>/dev/null)
            ;;
        sentences)
            if [[ $cur == -* ]]; then
                COMPREPLY=($(compgen -W "--skip-duplicates -h --help" -- "$cur"))
            else
                _filedir md
            fi
            ;;
        subs)
            case $prev in
                --lang) COMPREPLY=($(compgen -W "de en" -- "$cur")); return ;;
            esac
            if [[ $cur == -* ]]; then
                COMPREPLY=($(compgen -W "--lang --overwrite -h --help" -- "$cur"))
            else
                _filedir '@(mp4|mkv|webm|avi|mov|m4v|mp3|m4a|opus|ogg|flac|wav|MP4|MKV)'
            fi
            ;;
        words)
            [[ $prev == --min-words || $prev == --max-words ]] && return
            COMPREPLY=($(compgen -W "--min-words --max-words -h --help" -- "$cur"))
            ;;
        add-row)
            [[ $prev == --min-words || $prev == --max-words ]] && return
            COMPREPLY=($(compgen -W "--min-words --max-words --no-worker -h --help" -- "$cur"))
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
complete -F _danki danki
