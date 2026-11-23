"""Commercials: an original, generated pool of 90s/2000s-style ads, channel IDs and
promos for the channel's own shows, plus any folder of ad clips the owner already has.

Like the up-next bumpers, generated ads are short carrier clips (black video + an
original synth jingle) that TV mode draws over live (tv/ads_paint.py), so nothing
copyrighted is ever reproduced and promos always show current information.

Per channel, FS42's `commercial_dir` points at fs42/ads/<channel>/, a folder of
symlinks: the generated ads and IDs, one promo per show (promo_<tag>.mp4, described
in promos.json) and the owner's own clips (own_*). Media is never copied or touched.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path

from . import artwork, media_index, paths

_log = logging.getLogger("getsome.ads")
ADS_VERSION = "5"            # bump to regenerate everyone's ad carrier clips (5: a third of the volume)
# bump when the cards in a channel's pool change (new kinds, trivia wording): channels with
# commercial breaks then show "needs build" (channel_writer.channel_hash)
POOL_VERSION = "2"
AD_VOLUME = 0.33            # generated ads, cards, IDs and stings sit well under the shows
PROMO_SECONDS = 20
VIDEO_EXT = (".mp4", ".mkv", ".avi", ".mov", ".m4v", ".webm", ".mpg", ".mpeg", ".ts", ".wmv", ".flv")


@dataclass(frozen=True)
class Ad:
    name: str          # product
    kind: str          # food | soda | tech | kids | local | hero
    seconds: int
    tagline: str
    lines: tuple[str, ...]
    closer: str        # price, phone number or sign-off
    colors: tuple[str, str]
    hook: str = ""     # opening question / problem
    quote: str = ""    # testimonial
    who: str = ""      # who said it
    slam: str = ""     # price or offer burst


# All products, slogans and numbers are invented (555-01xx numbers are reserved for fiction).
ADS: list[Ad] = [
    Ad("Glowberry Crunch", "food", 30, "The cereal that glows in your tummy!",
       ("NEW marshmallow comets", "Part of a complete breakfast"), "Look for the purple box!", ("#b026ff", "#ffe600"), 'Is your breakfast BORING?', "It's like eating a galaxy!", 'Kevin, age 9', 'NEW!'),
    Ad("Hyperdrive Cola", "soda", 30, "Taste the speed of light",
       ("30% more fizz", "Ice cold. Ultra bold."), "Grab a can of the future", ("#ff2b2b", "#00f0ff"), 'Thirsty for something... faster?', 'Whoa. My tongue just broke the sound barrier.', 'Dana, skateboarder', 'ICE COLD'),
    Ad("MegaByte 486 DX", "tech", 30, "Your family's on-ramp to the information superhighway",
       ("8 MB RAM  ·  CD-ROM  ·  14.4k modem", "Windows-ready, out of the box"), "Only $1,999  ·  Call 1-800-555-0186",
       ("#2b6bff", "#7cff4f"), 'Still doing homework by hand?', 'Now my kids finish their homework AND play games!', 'The Wilson family', '$1,999'),
    Ad("LaserGrip Sneakers", "hero", 30, "Jump higher. Run faster. Glow brighter.",
       ("Light-up soles", "Pump-action fit"), "At sporting goods stores everywhere", ("#ff2bd6", "#00f0ff"), 'Want to fly?', "I've never jumped this high in my life!", 'Marcus, point guard', 'LIGHTS UP!'),
    Ad("Pixel Pals", "kids", 30, "The virtual pet that lives on your keychain!",
       ("Feed it!  Play with it!", "Don't forget it!"), "Collect all 6 colors!", ("#ffe600", "#ff2bd6"), "Everyone's got one. Do YOU?", "Mine's named Sparkles and she never gets sick!", 'Ashley, age 10', 'ONLY $14.99'),
    Ad("Thunder Pizza", "local", 15, "Hot to your door in 30 minutes or it's FREE",
       ("Two large one-toppings $9.99",), "Call 555-0142", ("#ff6b1a", "#ffe600"), 'Hungry? Of course you are.', 'The cheese! The CHEESE!', 'Tony, a satisfied customer', '$9.99'),
    Ad("Starlite Video", "local", 15, "Hundreds of new releases every Tuesday",
       ("Rent 2, get 1 free on weekends",), "Please rewind before returning!", ("#00f0ff", "#ff2bd6"), 'Friday night plans?', 'They had the new release in stock. Every copy!', 'Pat, movie buff', '2 FOR $3'),
    Ad("ZapTone Pager", "tech", 15, "Never miss a page again",
       ("Free activation this month",), "$49.99 at ZapTone dealers", ("#7cff4f", "#00f0ff"), 'Where ARE you?', 'My boss can reach me anywhere. Wait... is that good?', 'Steve, account manager', '$49.99'),
    Ad("Turbo Toast", "food", 30, "The toaster pastry with an attitude",
       ("Frosted. Sprinkled.", "TURBOCHARGED."), "In the breakfast aisle now", ("#ff2b2b", "#ffe600"), 'Breakfast at the speed of life.', 'Two minutes in the toaster. Zero seconds on my plate.', 'Jordan, age 12', 'TURBO!'),
    Ad("Dino-Mite Fruit Snacks", "kids", 30, "Fruit snacks with a ROAR!",
       ("Made with real fruit juice", "6 prehistoric shapes"), "Rawr-some!", ("#7cff4f", "#ff6b1a"), "What's in YOUR lunchbox?", 'I traded my sandwich for six of these!', 'Tyler, age 8', 'RAWR!'),
    Ad("Sunset Mall", "local", 15, "Over 100 stores under one roof",
       ("Open till 10 on Fridays",), "Exit 12 off the interstate", ("#ffb000", "#ff2bd6"), 'Bored this weekend?', 'We came for shoes and stayed for the food court.', 'The Garcia twins', 'OPEN LATE'),
    Ad("Chrome Wave Hair Gel", "hero", 15, "Maximum hold for maximum style",
       ("Now in Arctic Blast",), "Chrome Wave. Get the look.", ("#00f0ff", "#b026ff"), 'Bad hair day? Never again.', 'It held through a roller coaster. Twice.', 'Nikki, cheer captain', 'MAX HOLD'),
    Ad("Cosmo Kart Turbo Track", "kids", 30, "Loop-the-loop racing action!",
       ("Glow-in-the-dark track", "Twin launchers"), "Cars and batteries sold separately", ("#ff2bd6", "#ffe600"), 'Ready... set...', 'It does a loop! A REAL LOOP!', 'Danny, age 7', 'NEW!'),
    Ad("NovaCam 8", "tech", 30, "Catch every moment in brilliant color",
       ("12x zoom  ·  flip-out screen", "Fits in your hand"), "Now $799 at electronics stores", ("#b026ff", "#00f0ff"), "Don't just remember it. Record it.", 'Now I can tape the whole recital!', 'Linda, proud mom', '$799'),
]
IDS = [5, 5, 10, 2, 3]    # channel-ID stings ("You're watching channel N"), seconds


BUMP_SECONDS = 4
BUMPS_EACH = 3


def break_bumps_dir() -> Path:
    """FS42 bump folder for ad channels: pre/ (we'll be right back) and post/ (back to the show)."""
    return paths.fs42_work_dir() / "runtime" / "breakbumps"


def ads_dir() -> Path:
    return paths.fs42_work_dir() / "runtime" / "ads"


def promo_jingle(i: int) -> Path:
    return ads_dir() / f"promo_jingle_{i + 1}.mp4"


def channel_dir(slug: str) -> Path:
    return paths.fs42_work_dir() / "ads" / slug


CARD_LENGTHS = (15, 20)
CARD_VARIANTS = 2
BED_SECONDS = 60          # intermission music beds


def card_clip(genre: str, seconds: int, variant: int) -> Path:
    return ads_dir() / f"card_{genre}_{seconds}_{variant}.mp4"


def beds_dir() -> Path:
    return paths.fs42_work_dir() / "runtime" / "beds"


def intermission_beds() -> list[Path]:
    from . import beds

    mine = set(beds.edition_genres())         # XCable: jazz only; Neon Cable 42: its six styles
    return sorted(p for p in beds_dir().glob("*.m4a") if p.stem.rsplit("_", 1)[0] in mine)


def ensure_clips(force: bool = False) -> None:
    """Carrier clips (black video + original music) for every ad, card, ID and sting, plus the
    intermission music beds. A minute or two the first time, then cached."""
    import subprocess

    from . import assets_gen, beds, synth

    d = ads_dir()
    d.mkdir(parents=True, exist_ok=True)
    stamp = d / ".version"
    if stamp.exists() and stamp.read_text().strip() != ADS_VERSION:
        force = True
    G = beds.edition_genres()
    # (file name, seconds, music function)
    jobs = [(f"ad_{i + 1:02d}.mp4", a.seconds, lambda s, i=i: beds.compose(G[i % len(G)], 100 + i, s))
            for i, a in enumerate(ADS)]
    for gi, g in enumerate(G):
        for secs in CARD_LENGTHS:
            for v in range(CARD_VARIANTS):
                jobs.append((card_clip(g, secs, v + 1).name, secs,
                             lambda s, g=g, seed=600 + gi * 10 + secs + v: beds.compose(g, seed, s)))
    sting = ((lambda seed, s: beds.compose(G[seed % len(G)], seed, s)) if paths.XCABLE   # jazz stings
             else (lambda seed, s: synth.ad_jingle(seed, s)))
    jobs += [(f"id_{i + 1}.mp4", s_, lambda s, i=i: sting(200 + i, s)) for i, s_ in enumerate(IDS)]
    bb = break_bumps_dir()
    for sub in ("pre", "post"):
        (bb / sub).mkdir(parents=True, exist_ok=True)
    jobs += [(f"../breakbumps/pre/brb_{i + 1}.mp4", BUMP_SECONDS, lambda s, i=i: sting(400 + i, s))
             for i in range(BUMPS_EACH)]
    jobs += [(f"../breakbumps/post/back_{i + 1}.mp4", BUMP_SECONDS, lambda s, i=i: sting(500 + i, s))
             for i in range(BUMPS_EACH)]
    ff = assets_gen._ffmpeg()
    for name, secs, music in jobs:
        dst = d / name
        if dst.exists() and not force:
            continue
        _log.info("generating %s", name)
        wav = d / f".{Path(name).name}.wav"
        synth.write_wav(music(float(secs)), wav)
        subprocess.run([ff, "-y", "-loglevel", "error", "-f", "lavfi",
                        "-i", f"color=c=black:size=320x180:rate=10:duration={secs}",
                        "-i", str(wav), *assets_gen.video_codec_args("low"), "-af", f"volume={AD_VOLUME}",
                        "-c:a", "aac", "-b:a", "128k",
                        "-t", str(secs), str(dst)], check=True)
        wav.unlink()
    bd = beds_dir()
    bd.mkdir(parents=True, exist_ok=True)
    for gi, g in enumerate(G):
        for v in range(2):
            dst = bd / f"{g}_{v + 1}.m4a"
            if dst.exists() and not force:
                continue
            _log.info("generating %s", dst.name)
            wav = bd / f".{dst.stem}.wav"
            synth.write_wav(beds.compose(g, 900 + gi * 10 + v, BED_SECONDS), wav)
            subprocess.run([ff, "-y", "-loglevel", "error", "-i", str(wav), "-c:a", "aac", "-b:a", "160k",
                            str(dst)], check=True)
            wav.unlink()
    stamp.write_text(ADS_VERSION)


# --------------------------------------------------------------------------- promos

def _poster(folder: Path) -> str:
    for name in ("poster.jpg", "poster.png", "folder.jpg", "folder.png", "cover.jpg"):
        if (folder / name).is_file():
            return str(folder / name)
    for f in sorted(folder.glob("*-poster.*")):
        return str(f)
    return ""


def _fanart(folder: Path) -> str:
    for name in ("fanart.jpg", "fanart.png", "backdrop.jpg", "landscape.jpg"):
        if (folder / name).is_file():
            return str(folder / name)
    return ""


def show_promo_info(folder: Path, title: str) -> dict:
    """Title, year, genres, plot and local artwork from the show's tvshow.nfo (TinyMediaManager)."""
    info = {"title": title, "year": "", "genres": [], "plot": "", "poster": _poster(folder),
            "fanart": _fanart(folder)}
    nfo = folder / "tvshow.nfo"
    if nfo.is_file():
        try:
            xml = nfo.read_text(errors="replace")
        except OSError:
            xml = ""

        def tag(t: str) -> str:
            m = re.search(rf"<{t}>\s*(.*?)\s*</{t}>", xml, re.S | re.I)
            return re.sub(r"\s+", " ", m.group(1)).strip() if m else ""

        def unesc(s: str) -> str:
            return (s.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
                    .replace("&quot;", '"').replace("&apos;", "'"))

        info["year"] = tag("year") or tag("premiered")[:4]
        info["plot"] = unesc(tag("plot") or tag("outline"))
        info["genres"] = [unesc(g.strip()) for g in re.findall(r"<genre>\s*(.*?)\s*</genre>", xml, re.S | re.I)][:3]
    return info


# --------------------------------------------------------------------------- trivia from NFOs

def _xml_tag(xml: str, t: str) -> str:
    m = re.search(rf"<{t}>\s*(.*?)\s*</{t}>", xml, re.S | re.I)
    return _unesc(re.sub(r"\s+", " ", m.group(1)).strip()) if m else ""


def _unesc(s: str) -> str:
    return (s.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
            .replace("&quot;", '"').replace("&apos;", "'"))


def _read(p: Path) -> str:
    try:
        return p.read_text(errors="replace")
    except OSError:
        return ""


def _date_text(iso: str) -> str:
    import datetime as dt

    try:
        d = dt.date.fromisoformat(iso[:10])
    except ValueError:
        return ""
    return f"{d.strftime('%B')} {d.day}, {d.year}"


def _actors(xml: str, limit: int = 3) -> list[tuple[str, str]]:
    out = []
    for block in re.findall(r"<actor>(.*?)</actor>", xml, re.S | re.I):
        name, role = _xml_tag(block, "name"), _xml_tag(block, "role")
        if name and role and "/" not in role and len(role) < 40:
            out.append((name, role))
        if len(out) >= limit:
            break
    return out


def _actor_names(xml: str) -> list[str]:
    names = []
    for block in re.findall(r"<actor>(.*?)</actor>", xml, re.S | re.I):
        n = _unesc(_xml_tag(block, "name")).strip()
        if n and n not in names:
            names.append(n)
    return names


def episode_details(path: str) -> tuple[str, str]:
    """(title, plot) of a scheduled episode, from the NFO next to the real file."""
    import os

    try:
        xml = _read(Path(os.path.realpath(path)).with_suffix(".nfo"))
    except OSError:
        return "", ""
    return _unesc(_xml_tag(xml, "title")), _unesc(_xml_tag(xml, "plot"))


def show_facts(folder: Path, title: str, channel: int | str = "") -> tuple[list[str], list[dict]]:
    """Trivia lines for a show, and its episodes' air dates (for "On this day" cards), all
    from the owner's own NFO files. Returns (facts, [{date, title, code}...])."""
    from collections import Counter

    from . import media_index

    xml = _read(folder / "tvshow.nfo")
    facts: list[str] = []
    premiered, studio = _xml_tag(xml, "premiered"), _xml_tag(xml, "studio")
    if premiered and _date_text(premiered):
        facts.append(f"{title} premiered on {_date_text(premiered)}" + (f" on {studio}." if studio else "."))
    cast = _actors(xml, 12)
    roles = dict(cast)
    on = f"here on Channel {channel}" if channel else f"here on {paths.APP_NAME}"
    tagline = _xml_tag(xml, "tagline")
    if tagline:
        facts.append(f"“{tagline}”")
    aired: list[dict] = []
    directors: Counter = Counter()
    stars: Counter = Counter()
    seasons: Counter = Counter()
    try:
        eps = media_index.scan_show(folder).episodes
    except OSError:
        eps = []
    first = None
    for e in eps:
        exml = _read(e.path.with_suffix(".nfo"))
        if e.season:
            seasons[e.season] += 1
        if not exml:
            continue
        d, t = _xml_tag(exml, "aired"), _xml_tag(exml, "title")
        stars.update(_actor_names(exml))
        dr = _xml_tag(exml, "director")
        if dr:
            directors[dr] += 1
        if d and len(d) >= 10 and e.season:
            code = f"S{e.season:02d}E{(e.episode or 0):02d}"
            aired.append({"date": d[:10], "title": t, "code": code})
            if first is None or d < first[0]:
                first = (d, t, code)
    if len(seasons) > 1 and aired:
        years = sorted(a["date"][:4] for a in aired)
        facts.append(f"{title} ran for {len(seasons)} seasons" +
                     (f", from {years[0]} to {years[-1]}." if years[0] != years[-1] else "."))
    if first and first[1] and first[2].startswith("S01"):
        facts.append(f"The very first episode, “{first[1]}”, aired on {_date_text(first[0])}.")
    if directors:
        dr, k = directors.most_common(1)[0]
        if k >= 3:
            facts.append(f"{dr} directed {k} episodes of {title}.")
    # "<actor> stars in N episodes": counted from the episode NFOs; if those list no cast,
    # the show's main cast from tvshow.nfo
    picked = [(n, k) for n, k in stars.most_common(4) if k >= 2]
    for name, k in picked:
        who = f"{name} ({roles[name]})" if name in roles else name
        many = f"all {k} episodes" if eps and k >= len(eps) else f"{k} episodes"
        facts.append(f"{who} stars in {many} of {title}. Catch them all {on}!")
    if not picked:
        for name, role in cast[:3]:
            facts.append(f"{name} stars as {role} in {title}. Catch every episode {on}!")
    return facts, aired


def movie_card(path: str) -> dict:
    """Everything a "coming up" film card shows, for a scheduled film (a link in a channel's catalog):
    title, year, rating, runtime, genres, synopsis, tagline, trivia facts and local artwork."""
    import os

    from . import artwork, media_index

    # follow only the channel's own catalog link: the NFO and artwork sit next to the film as it
    # appears in the library (which may itself be organised with symlinks)
    real = Path(os.path.realpath(path))
    try:                                  # the film as the library knows it (the link may skip symlinks)
        from . import library

        name = Path(path).name
        for m in library.cached_movies() or []:
            if Path(m.path).name == name or os.path.realpath(m.path) == str(real):
                real = Path(m.path)
                if Path(m.path).name == name:
                    break
    except Exception:
        pass
    out: dict = {"title": real.stem, "year": "", "rating": "", "runtime": "", "genres": [], "plot": "",
                 "tagline": "", "facts": [], "art": {}}
    try:
        mv = media_index.read_movie(real)
        out.update(title=mv.title, year=str(mv.year or ""), rating=mv.rating or "", genres=list(mv.genres or [])[:3])
        if mv.runtime:
            h, m = divmod(mv.runtime, 60)
            out["runtime"] = f"{h}h {m:02d}m" if h else f"{m}m"
    except OSError:
        pass
    xml = _read(real.with_suffix(".nfo"))
    if not xml:
        for alt in real.parent.glob("*.nfo"):
            xml = _read(alt)
            break
    out["plot"] = _unesc(_xml_tag(xml, "plot"))
    out["tagline"] = _unesc(_xml_tag(xml, "tagline"))
    try:
        out["facts"] = movie_facts(real)
    except OSError:
        pass
    try:
        out["art"] = artwork.movie_art(str(real))
    except OSError:
        pass
    return out


def movie_facts(path: Path) -> list[str]:
    xml = _read(path.with_suffix(".nfo"))
    if not xml:
        for alt in path.parent.glob("*.nfo"):
            xml = _read(alt)
            break
    title, year = _xml_tag(xml, "title") or path.stem, _xml_tag(xml, "year")
    name = f"{title} ({year})" if year else title
    facts = []
    dr = _xml_tag(xml, "director")
    if dr:
        facts.append(f"{name} was directed by {dr}.")
    tagline = _xml_tag(xml, "tagline")
    if tagline:
        facts.append(f"{title}: “{tagline}”")
    for actor, role in _actors(xml, 2):
        facts.append(f"In {title}, {role} is played by {actor}.")
    studio = _xml_tag(xml, "studio")
    if studio and year:
        facts.append(f"{title} was released by {studio} in {year}.")
    return facts


# --------------------------------------------------------------------------- per-channel pool

PRODUCTS_FEW = 5          # "a few" product ads per channel
STILLS_PER_CARD = 6


def _stills(thumbs: list[tuple[str, str]]) -> list[dict]:
    """Episode stills with their code and title (from the episode's NFO next to it)."""
    out = []
    for thumb, stem in thumbs:
        m = re.search(r"[Ss](\d{1,2})[Ee](\d{1,3})", stem)
        code = f"S{int(m.group(1)):02d}E{int(m.group(2)):02d}" if m else ""
        name = ""
        nfo = Path(thumb).with_name(stem + ".nfo")
        if nfo.is_file():
            name = _unesc(_xml_tag(_read(nfo), "title"))
        out.append({"thumb": thumb, "code": code, "name": name})
    return out
FACTS_PER_SHOW = 4
FACTS_MAX = 48


def link_pool(slug: str, number: int, shows: list[tuple[str, str, str]], own_folder: str = "",
              movies: list[str] | None = None, products: str = "few") -> Path:
    """(Re)build fs42/ads/<slug>/. shows: (tag, folder, title). Cards (promos, trivia, tonight's
    lineup, on this day) make up most of the pool; product ads are 'none', 'few' or 'lots'."""
    import random

    from . import beds

    ensure_clips()
    d = channel_dir(slug)
    d.mkdir(parents=True, exist_ok=True)
    for old in d.iterdir():
        if old.is_symlink() or old.suffix == ".json":
            old.unlink()
    src = ads_dir()
    rng = random.Random(slug)
    prods = sorted(src.glob("ad_*.mp4"))
    if products == "none":
        prods = []
    elif products != "lots":
        prods = rng.sample(prods, min(PRODUCTS_FEW, len(prods)))
    for f in prods + sorted(src.glob("id_*.mp4")):
        (d / f.name).symlink_to(f)

    # card clips rotate through the genres so neighbouring cards sound different
    carriers = {L: [card_clip(g, L, v + 1) for v in range(CARD_VARIANTS) for g in beds.edition_genres()]
                for L in CARD_LENGTHS}
    turn = {L: rng.randrange(len(carriers[L])) for L in CARD_LENGTHS}

    def card(name: str, seconds: int, data: dict) -> None:
        lst = carriers[seconds]
        target = lst[turn[seconds] % len(lst)]
        turn[seconds] += 1
        (d / name).symlink_to(target)
        cards[name] = data

    cards: dict[str, dict] = {}
    posters: list[str] = []
    facts: list[dict] = []
    today: dict[str, list[dict]] = {}
    for tag, folder, title in shows:
        info = {**show_promo_info(Path(folder), title), "tag": tag, "channel": number}
        card(f"card_promo_{tag}.mp4", 20, {"kind": "promo", **info})
        # artwork cards: a spotlight on the show's backdrop + logo, and a filmstrip of episode stills
        art = artwork.show_art(str(folder))
        look = {**info, "logo": art["logo"], "fanart": art["fanart"] or art["landscape"]}
        if look["fanart"] or look["logo"]:
            card(f"card_spot_{tag}.mp4", 15, {**look, "kind": "spotlight"})
        if len(art["thumbs"]) >= 3:
            picks = sorted(rng.sample(art["thumbs"], min(STILLS_PER_CARD, len(art["thumbs"]))))
            card(f"card_stills_{tag}.mp4", 20, {**look, "kind": "stills", "stills": _stills(picks)})
        if info["poster"]:
            posters.append(info["poster"])
        try:
            fl, aired = show_facts(Path(folder), title, number)
        except OSError:
            fl, aired = [], []
        rng.shuffle(fl)
        art = {"title": title, "poster": info["poster"], "fanart": info["fanart"]}
        facts += [{"kind": "trivia", "fact": f, **art} for f in fl[:FACTS_PER_SHOW]]
        for a in aired:
            today.setdefault(a["date"][5:], []).append({**a, "show": title, **art})
    for m in movies or []:
        mp = artwork.movie_art(m)["poster"]
        if mp:
            posters.append(mp)
        for f in movie_facts(Path(m))[:2]:
            facts.append({"kind": "trivia", "fact": f, "title": Path(m).stem, "poster": _poster(Path(m).parent),
                          "fanart": _fanart(Path(m).parent)})
    rng.shuffle(facts)
    for k, f in enumerate(facts[:FACTS_MAX]):
        card(f"card_fact_{k + 1:03d}.mp4", 15, f)
    for k in range(3):
        card(f"card_lineup_{k + 1}.mp4", 15, {"kind": "lineup", "channel": number})
    if len(posters) >= 2:
        rng.shuffle(posters)
        for k in range(2):
            card(f"card_wall_{k + 1}.mp4", 15, {"kind": "wall", "channel": number, "posters": posters[:60]})
    if today:
        for k in range(2):
            card(f"card_today_{k + 1}.mp4", 15, {"kind": "onthisday", "channel": number})
    (d / "cards.json").write_text(json.dumps({"cards": cards, "onthisday": today}, indent=1))

    # the owner's own commercials: this channel's folder, then the shared ones from Settings
    seen: set[str] = set()
    n = 0
    for folder in [own_folder] + commercial_folders():
        if not folder or not Path(folder).is_dir():
            continue
        for f in sorted(Path(folder).rglob("*")):
            if (f.is_file() and f.suffix.lower() in media_index.VIDEO_EXTS and not f.name.startswith(".")
                    and str(f) not in seen):
                seen.add(str(f))
                n += 1
                (d / f"own_{n:03d}{media_index.fs42_suffix(f)}").symlink_to(f)
    return d


def commercial_folders() -> list[str]:
    """Settings → Commercial folders: the owner's own commercials, for every channel with breaks."""
    from . import settings

    return [f for f in settings.load().get("commercial_folders", []) if f]


_sig_cache: tuple[float, str] | None = None


def commercials_signature() -> str:
    """Changes when a commercial folder is added/removed or a clip is added/removed in one (every
    folder's modification time, subfolders included), so those channels show "needs build"."""
    global _sig_cache
    import os
    import time

    if _sig_cache and time.monotonic() - _sig_cache[0] < 10:
        return _sig_cache[1]
    parts = []
    for folder in commercial_folders():
        parts.append(folder)
        for root, dirs, _files in os.walk(folder):
            dirs[:] = [x for x in dirs if not x.startswith(".")]
            try:
                parts.append(f"{root}:{os.stat(root).st_mtime_ns}")
            except OSError:
                pass
    sig = "|".join(parts)
    _sig_cache = (time.monotonic(), sig)
    return sig


def kind_of(path: str) -> tuple[str, str] | None:
    """("ad", "3") / ("id", "1") / ("promo", "promo_X.mp4") / ("brb"|"back", "1") for a
    generated clip, else None."""
    m = re.search(r"/breakbumps/(?:pre|post)/(brb|back)_(\d+)\.mp4$", path)
    if m:
        return m.group(1), m.group(2)
    m = re.search(r"/ads/[^/]+/(ad|id)_(\d+)\.mp4$", path)
    if m:
        return m.group(1), m.group(2)
    m = re.search(r"/ads/[^/]+/(card_[^/]+\.mp4)$", path)
    if m:
        return "card", m.group(1)
    m = re.search(r"/ads/[^/]+/(promo_[^/]+\.mp4)$", path)       # first version
    if m:
        return "promo", m.group(1)
    return None


_json_cache: dict[str, tuple[float, dict]] = {}


def _channel_json(path: str, name: str) -> dict:
    d = Path(path).parent / name
    try:
        mtime = d.stat().st_mtime
        cached = _json_cache.get(str(d))
        if not cached or cached[0] != mtime:
            _json_cache[str(d)] = (mtime, json.loads(d.read_text()))
        return _json_cache[str(d)][1]
    except (OSError, ValueError):
        return {}


def card_for(path: str) -> dict:
    return _channel_json(path, "cards.json").get("cards", {}).get(Path(path).name, {})


def on_this_day(path: str, when=None) -> tuple[str, list[dict]]:
    """("ON THIS DAY" | "THIS WEEK IN TV HISTORY", episodes that first aired then)."""
    import datetime as dt

    table = _channel_json(path, "cards.json").get("onthisday", {})
    day = when or dt.date.today()
    items = table.get(day.strftime("%m-%d"), [])
    if items:
        return "ON THIS DAY", items
    week = []
    for k in range(-3, 4):
        week += table.get((day + dt.timedelta(days=k)).strftime("%m-%d"), [])
    return "THIS WEEK IN TV HISTORY", week


_promo_cache: dict[str, tuple[float, dict]] = {}


def promo_for(path: str) -> dict:
    d = Path(path).parent / "promos.json"
    try:
        mtime = d.stat().st_mtime
        cached = _promo_cache.get(str(d))
        if not cached or cached[0] != mtime:
            _promo_cache[str(d)] = (mtime, json.loads(d.read_text()))
        return _promo_cache[str(d)][1].get(Path(path).name, {})
    except (OSError, ValueError):
        return {}
