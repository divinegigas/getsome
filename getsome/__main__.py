"""GetSome command line.

    getsome                         setup window (or TV mode if "launch into TV" is on)
    getsome setup                   setup window
    getsome tv                      fullscreen TV mode
    getsome demo                    generate a demo library + channels and build them
    getsome scan FOLDER             show what Neon Cable 42 finds in a TV folder
    getsome add-channel N NAME DIR [DIR...] [--sequential]
    getsome remove-channel NAME
    getsome list                    channels and how far their schedules run
    getsome build [NAME...]         rebuild channels (all, or just those named)
    getsome extend                  add another week to every schedule
    getsome upkeep                  top up schedules running short, rebuild ones that ended
    getsome add-to-menu             put Neon Cable 42 in the desktop's app menu (remove-from-menu undoes it)
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(level=logging.DEBUG if verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if not verbose:
        # FS42 is chatty at INFO
        for noisy in ("fs42", "FluidBuilder", "LiquidIO", "MEDIA", "CatalogAPI", "StationManager", "SEQUENCE"):
            logging.getLogger(noisy).setLevel(logging.WARNING)


def _prepare() -> None:
    from . import assets_gen, fs42

    assets_gen.ensure_runtime_assets()
    fs42.init()


def _build(names: list[str] | None = None, verbose: bool = False) -> int:
    from . import builder, model

    chans = model.load()
    todo = chans if not names else [c for c in chans if c.name.lower() in {n.lower() for n in names}]
    missing = set(n.lower() for n in names or []) - {c.name.lower() for c in todo}
    for m in missing:
        print(f"No channel named {m!r}")
    if not todo:
        print("Nothing to build.")
        return 1
    log = (lambda line: print(line)) if verbose else (lambda line: None if line.startswith("    ") else print(line))
    failures = builder.build(todo, chans, log=log)
    print("Build finished." if not failures else f"Build finished with {len(failures)} failure(s).")
    return 1 if failures else 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="getsome", description="90s cable TV from your media library")
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="cmd")

    sub.add_parser("setup", help="setup window")

    tv = sub.add_parser("tv", help="fullscreen TV mode")
    tv.add_argument("--windowed", action="store_true", help="run in a window instead of fullscreen")
    tv.add_argument("--channel", type=int, help="start on this channel number")

    sub.add_parser("demo", help="generate demo media and channels, then build")

    sc = sub.add_parser("scan", help="show what Neon Cable 42 finds in a TV folder (changes nothing)")
    sc.add_argument("folder", type=Path)

    add = sub.add_parser("add-channel", help="add a 24-hour channel from show folders")
    add.add_argument("number", type=int)
    add.add_argument("name")
    add.add_argument("folders", nargs="+", type=Path)
    add.add_argument("--sequential", action="store_true", help="play each show in order (default: shuffle)")
    add.add_argument("--no-build", action="store_true")

    rm = sub.add_parser("remove-channel", help="delete a channel")
    rm.add_argument("name")

    sub.add_parser("compose-music", help="compose any missing guide music tracks (runs in the background normally)")

    sub.add_parser("list", help="list channels")
    b = sub.add_parser("build", help="rebuild channels (all, or the ones named)")
    b.add_argument("names", nargs="*", help='channel names, e.g. "Trek" "Toons"')
    sub.add_parser("extend", help="add a week to every schedule")
    sub.add_parser("add-to-menu", help="add Neon Cable 42 to the desktop app menu (Sound & Video)")
    sub.add_parser("remove-from-menu", help="remove Neon Cable 42 from the desktop app menu")
    sub.add_parser("upkeep", help="extend schedules running short and rebuild any that ended")

    args = p.parse_args(argv)
    _setup_logging(args.verbose)
    from . import steam

    steam.prepare_qt()          # before any QApplication exists
    _prepare()

    cmd = args.cmd
    if cmd is None:
        from . import settings

        # started from Steam (Big Picture, couch): straight to TV, like "launch straight into TV"
        cmd = "tv" if settings.load().get("launch_into_tv") or steam.from_steam() else "setup"

    if cmd == "setup":
        from .gui.main_window import run_setup

        return run_setup()

    if cmd == "tv":
        from .tv.app import run_tv

        return run_tv(windowed=getattr(args, "windowed", False), start_channel=getattr(args, "channel", None))

    if cmd == "demo":
        from . import demo

        demo.make_demo()
        return _build(verbose=args.verbose)

    if cmd == "compose-music":
        from . import assets_gen

        rc = assets_gen.compose_all()
        ready = [n for k, n, _p in assets_gen.ready_tracks()]
        print(f"{len(ready)} guide tracks ready: {', '.join(ready)}")
        return rc

    if cmd == "scan":
        from . import media_index

        shows = media_index.find_shows(args.folder)
        if not shows:
            print(f"No show folders found in {args.folder}")
            return 1
        print(f"{'Show':<44} {'Episodes':>8}  Seasons   Notes")
        for folder in shows:
            sh = media_index.scan_show(folder)
            seasons = sh.seasons
            srange = f"{seasons[0]}-{seasons[-1]}" if len(seasons) > 1 else (str(seasons[0]) if seasons else "-")
            notes = []
            if sh.skipped_specials:
                notes.append(f"{sh.skipped_specials} specials skipped")
            if sh.unnumbered:
                notes.append(f"{sh.unnumbered} without season/episode numbers (played last)")
            if not sh.episodes:
                notes.append("NO EPISODES FOUND")
            print(f"{sh.title[:44]:<44} {len(sh.episodes):>8}  {srange:<9} {', '.join(notes)}")
        return 0

    if cmd == "add-channel":
        from . import channel_writer, model

        chans = model.load()
        ch = model.find(chans, args.name)
        mode = model.SEQUENTIAL if args.sequential else model.SHUFFLE
        shows = []
        for f in args.folders:
            f = f.expanduser().resolve()
            if not f.is_dir():
                print(f"Error: not a folder: {f}")
                return 1
            shows.append(model.ShowRef(folder=str(f)))
        if ch is None:
            ch = model.Channel(number=args.number, name=args.name)
            chans.append(ch)
        ch.number, ch.mode, ch.shows = args.number, mode, shows
        ch.template = "marathon" if args.sequential else "shuffle"
        try:
            channel_writer.check_number(ch, chans)
        except channel_writer.ChannelNumberTaken as e:
            print(f"Error: {e} Pick another number, or remove it first.")
            return 1
        model.save(chans)
        print(f"Saved channel {ch.number} {ch.name} ({len(shows)} shows, {mode})")
        return 0 if args.no_build else _build([ch.name], args.verbose)

    if cmd == "remove-channel":
        from . import builder, model

        chans = model.load()
        ch = model.find(chans, args.name)
        if ch is None:
            print(f"No channel named {args.name!r}")
            return 1
        builder.remove_channel(ch, chans)
        print(f"Removed {ch.name}")
        return 0

    if cmd == "list":
        import datetime as dt

        from . import channel_writer, fs42, model

        chans = model.load()
        if not chans:
            print("No channels yet. Run  ./getsome.sh  to open the setup window.")
        numbers = [c.number for c in chans]
        for c in chans:
            end = fs42.schedule_end(c.name) if c.built_hash else None
            state = f"scheduled until {end:%a %d %b %H:%M}" if end else "not built"
            if c.built_hash and (end is None or end < dt.datetime.now()):
                state = "schedule ended (run: getsome upkeep)"
            if c.built_hash and channel_writer.preview_hash(c) != c.built_hash:
                state += " (changed - needs build)"
            dup = "   <- duplicate number" if numbers.count(c.number) > 1 else ""
            print(f"{c.number:>4}  {c.name:<24} {c.mode:<10} {c.content_text:>10}  {state}{dup}")
        return 0

    if cmd == "build":
        return _build(args.names or None, args.verbose)

    if cmd in ("add-to-menu", "remove-from-menu"):
        from . import desktop

        if cmd == "add-to-menu":
            print(f"Added to the app menu: {desktop.install()}")
        else:
            desktop.remove()
            print("Removed from the app menu.")
        return 0

    if cmd == "upkeep":
        from . import upkeep

        names = upkeep.run(log=print)
        print(f"Updated: {', '.join(names)}" if names else "All schedules are good for the next few days.")
        return 0

    if cmd == "extend":
        from . import fs42

        return fs42.extend_schedules()

    p.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
