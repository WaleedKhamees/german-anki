"""german-anki command line. Heavy modules are imported lazily so `sync` stays fast."""
import argparse
import sys


def _positive_int(value):
    n = int(value)
    if n < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return n


def build_parser():
    parser = argparse.ArgumentParser(prog="german-anki", description=__doc__)
    sub = parser.add_subparsers(dest="cmd")
    # example sentence length, for the commands that write word cards
    length = argparse.ArgumentParser(add_help=False)
    length.add_argument("--min-words", type=_positive_int, metavar="N",
                        help="shortest example sentence, in words (default: EXAMPLE_MIN_WORDS in .env)")
    length.add_argument("--max-words", type=_positive_int, metavar="N",
                        help="longest example sentence, in words (default: EXAMPLE_MAX_WORDS in .env)")
    sub.add_parser("words", parents=[length], help="create cards for the words in the clipboard (default)")
    p = sub.add_parser("add-row", parents=[length], help="create/update the card for one to-do row (used by mpv)")
    p.add_argument("row")
    p.add_argument("--no-worker", action="store_true",
                   help="process the row in this process instead of the background worker")
    sub.add_parser("serve", help="background worker that keeps the browser warm for add-row (started by add-row)")
    p = sub.add_parser("sentences", help="import sentence pairs (default file: <data>/sentence-mining.md)")
    p.add_argument("file", nargs="?", help="markdown table to import instead of the default file")
    p.add_argument("--skip-duplicates", action="store_true", help="skip pairs already in the deck")
    p = sub.add_parser("add-sentence", help="add one sentence row to the sentence deck (used by mpv)")
    p.add_argument("row")
    p = sub.add_parser("regen", parents=[length], help="regenerate existing word cards in place (review history is kept)",
                       description="Regenerate existing word cards in place; review history, Screenshot and "
                                   "SourceAudio are kept. Picks the notes tagged 'regen' in the word deck, "
                                   "or the given words, or --query. Close the note in Anki's browser first: "
                                   "AnkiConnect does not update a note that is open in the editor.")
    p.add_argument("words", nargs="*", help="words to regenerate (Word field, exact match)")
    p.add_argument("--query", help="Anki search within the word deck instead, e.g. 'added:7' or 'flag:1'")
    p.add_argument("--only", help="comma-separated parts to redo: definition,examples,conjugation,pronunciation "
                                  "(default: all)")
    p.add_argument("--hint", help="the meaning you want, passed to the LLM, e.g. 'ladder, not leader'")
    p.add_argument("--dry-run", action="store_true", help="list the matching notes without changing them")
    sub.add_parser("sync", help="export the deck for subtitle colouring (used by mpv)")
    sub.add_parser("list-words", help="print the deck's words from the last sync (used by shell completion)")
    sub.add_parser("backfill-tags", help="make no-screenshot / no-audio tags match the fields")
    p = sub.add_parser("setup-dict", help="download and build the German dictionary")
    p.add_argument("--zip", help="use this dict-de-de.zip instead of downloading")
    p.add_argument("--force", action="store_true", help="re-download / rebuild")
    p = sub.add_parser("setup-anki", help="create the missing decks and note type in Anki")
    p.add_argument("-y", "--yes", action="store_true", help="don't ask first")
    sub.add_parser("doctor", help="check the installation")
    sub.add_parser("paths", help="print the config file and data locations")
    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.cmd == "serve":
        from . import server
        return server.serve()
    if args.cmd == "add-row" and not args.no_worker:
        if args.min_words and args.max_words and args.min_words > args.max_words:
            parser.error(f"need 1 <= min words <= max words, got {args.min_words}-{args.max_words}")
        from . import server
        status = server.run_add_row({"row": args.row, "min_words": args.min_words, "max_words": args.max_words})
        if status is not None:
            return status
        print("[!] background worker unavailable, processing the row here", file=sys.stderr)
    if args.cmd in (None, "words", "add-row", "regen"):
        if args.cmd == "regen" and args.words and args.query:
            parser.error("give words or --query, not both")
        from . import cards, examples, verbformen
        try:
            examples.set_length(getattr(args, "min_words", None), getattr(args, "max_words", None))
            if args.cmd == "regen":
                cards.parse_parts(args.only)
        except ValueError as e:
            parser.error(str(e))
        try:
            if args.cmd == "add-row":
                return cards.process_row(args.row)
            if args.cmd == "regen":
                return cards.run_regen(args.words, args.query, args.only, args.hint, args.dry_run)
            cards.run_clipboard()
            return 0
        finally:
            verbformen.close()   # the browser is shared by all the words of the run
    if args.cmd == "list-words":
        import json
        from . import config
        try:
            words = json.loads(config.load().words_json.read_text(encoding="utf-8"))["words"]
        except (OSError, ValueError, KeyError):
            return 1
        print("\n".join(sorted(words, key=str.lower)))
        return 0
    if args.cmd == "backfill-tags":
        from . import cards
        cards.backfill_media_tags()
        return 0
    if args.cmd == "sentences":
        from . import sentences
        return sentences.run(args.file, skip_duplicates=args.skip_duplicates)
    if args.cmd == "add-sentence":
        from . import sentences
        return sentences.add_row(args.row)
    if args.cmd == "sync":
        from . import config, sync
        return sync.run(config.load())
    if args.cmd == "setup-dict":
        from . import config, dictionary
        dictionary.setup(config.load().dict_dir, zip_path=args.zip, force=args.force)
        return 0
    if args.cmd == "setup-anki":
        from . import anki_setup
        return anki_setup.run(assume_yes=args.yes)
    if args.cmd == "doctor":
        from . import doctor
        return doctor.run()
    if args.cmd == "paths":
        from . import config
        s = config.load()
        for label, value in [("config", config.CONFIG_PATH), ("data", s.data_dir), ("to-do", s.todo_file),
                             ("done", s.done_file), ("sentences", s.sentences_file),
                             ("sent-done", s.sentences_done_file), ("assets", s.assets_dir), ("dictionary", s.dict_dir),
                             ("review", s.review_file), ("cache", s.cache_dir)]:
            print(f"{label:10} {value}")
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
