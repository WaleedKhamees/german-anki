"""german-anki command line. Heavy modules are imported lazily so `sync` stays fast."""
import argparse
import sys


def main(argv=None):
    parser = argparse.ArgumentParser(prog="german-anki", description=__doc__)
    sub = parser.add_subparsers(dest="cmd")
    sub.add_parser("words", help="create cards for the words in the clipboard (default)")
    p = sub.add_parser("add-row", help="create/update the card for one to-do row (used by mpv)")
    p.add_argument("row")
    p = sub.add_parser("sentences", help="import sentence pairs (default file: <data>/sentence-mining.md)")
    p.add_argument("file", nargs="?", help="markdown table to import instead of the default file")
    p.add_argument("--skip-duplicates", action="store_true", help="skip pairs already in the deck")
    p = sub.add_parser("add-sentence", help="add one sentence row to the sentence deck (used by mpv)")
    p.add_argument("row")
    sub.add_parser("sync", help="export the deck for subtitle colouring (used by mpv)")
    sub.add_parser("backfill-tags", help="make no-screenshot / no-audio tags match the fields")
    p = sub.add_parser("setup-dict", help="download and build the German dictionary")
    p.add_argument("--zip", help="use this dict-de-de.zip instead of downloading")
    p.add_argument("--force", action="store_true", help="re-download / rebuild")
    sub.add_parser("doctor", help="check the installation")
    sub.add_parser("paths", help="print the config file and data locations")
    args = parser.parse_args(argv)

    if args.cmd in (None, "words"):
        from . import cards
        cards.run_clipboard()
        return 0
    if args.cmd == "add-row":
        from . import cards
        return cards.process_row(args.row)
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
