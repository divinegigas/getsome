"""Auto-create channels: group a library's shows (and, separately, films) into N channels of
similar content, using what the owner's NFO files say (genres, age rating, year).

It is a starting point, not a final lineup: the GUI shows the proposal for editing before
anything is created. Rules:
  * similarity = weighted overlap of genres, nudged by decade
  * family-rated and mature-rated titles never share a channel
  * merges are biased towards evenly sized channels
  * films only go on movie channels (TV channels are shows only)
Names are invented, descriptive, never real networks.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field

from . import media_index, model

INF = float("inf")

# genre words as TinyMediaManager / TMDB / TVDB write them -> one vocabulary
_SYNONYMS = {
    "science fiction": "sci-fi", "sci-fi": "sci-fi", "scifi": "sci-fi", "science-fiction": "sci-fi",
    "children": "kids", "kids": "kids", "children's": "kids", "childrens": "kids",
    "animation": "animation", "animated": "animation", "cartoon": "animation", "cartoons": "animation",
    "anime": "anime", "sitcom": "comedy", "comedy": "comedy", "suspense": "thriller",
    "talk": "talk", "talk show": "talk", "game show": "game show", "game-show": "game show",
    "politics": "war", "war": "war", "military": "war", "news": "news", "documentary": "documentary",
    "docuseries": "documentary", "reality": "reality", "reality-tv": "reality", "reality tv": "reality",
    "music": "music", "musical": "music", "sport": "sport", "sports": "sport", "soap": "soap",
    "romance": "romance", "family": "family", "history": "history", "horror": "horror",
    "action": "action", "adventure": "adventure", "crime": "crime", "mystery": "mystery",
    "thriller": "thriller", "drama": "drama", "fantasy": "fantasy", "western": "western",
}
_IGNORE = {"mini-series", "miniseries", "tv movie", "foreign", "home and garden", "special interest", ""}
# distinctive genres count for more than common ones (nearly everything is a "drama")
_WEIGHT = {"animation": 2.2, "kids": 2.0, "anime": 2.2, "documentary": 1.8, "reality": 1.6, "horror": 1.6,
           "comedy": 1.4, "sci-fi": 1.4, "western": 1.6, "talk": 1.6, "game show": 1.6, "news": 1.6,
           "music": 1.4, "sport": 1.6, "war": 1.3, "fantasy": 1.2, "drama": 0.7, "adventure": 0.8,
           "action": 0.9, "family": 1.1}
# shows of these genres are episodic: shuffling them works; the rest play in order
_EPISODIC = {"comedy", "animation", "kids", "reality", "talk", "game show", "documentary", "news", "sport"}

TV_NAMES = {
    "comedy": "Laugh Track", "animation": "Toon Zone", "kids": "Kid Zone", "anime": "Anime Arcade",
    "sci-fi": "Sci-Fi Zone", "fantasy": "Realm TV", "drama": "Drama Prime", "crime": "Crime Time",
    "mystery": "Mystery Hour", "thriller": "Edge TV", "action": "Action Alley", "adventure": "Action Alley",
    "horror": "Fright Night", "documentary": "Fact Files", "reality": "Real Life TV",
    "western": "Frontier TV", "war": "Battle Stations", "romance": "Heartbeat TV", "talk": "Talk Shop",
    "news": "News Desk", "music": "Jukebox TV", "sport": "Sports Desk", "family": "Family Room",
    "history": "Time Machine", "soap": "Daytime Drama", "game show": "Prize Time",
}
MOVIE_NAMES = {
    "comedy": "Comedy Movies", "horror": "Horror Night", "action": "Action Cinema", "adventure": "Adventure Cinema",
    "sci-fi": "Sci-Fi Cinema", "drama": "Drama Theater", "animation": "Family Movies", "family": "Family Movies",
    "kids": "Family Movies", "thriller": "Thriller Theater", "crime": "Crime Cinema", "romance": "Romance Movies",
    "documentary": "Documentary Theater", "western": "Western Movies", "war": "War Movies",
    "fantasy": "Fantasy Movies", "mystery": "Mystery Movies", "music": "Musicals", "history": "History Theater",
    "anime": "Anime Movies",
}


def genre_set(genres: list[str] | None) -> frozenset[str]:
    out = set()
    for g in genres or []:
        for part in re.split(r"\s*(?:&|/|,|\band\b)\s*", g.strip().lower()):
            part = part.strip()
            if part in _IGNORE:
                continue
            out.add(_SYNONYMS.get(part, part))
    return frozenset(out)


def year_of(text: str) -> int | None:
    m = re.search(r"\((\d{4})\)\s*$", text or "")
    return int(m.group(1)) if m else None


@dataclass
class Item:
    key: str                    # show folder or movie path
    title: str
    genres: frozenset[str]
    tier: str                   # family | pg | teen | mature | none
    year: int | None
    rating: str = ""
    movie: bool = False         # a film (key is a file), else a show (key is a folder)


@dataclass
class Proposal:
    name: str
    number: int
    kind: str                               # model.TV | model.MOVIES
    template: str                           # model template key
    items: list[Item] = field(default_factory=list)

    def channel(self, commercials: bool = False) -> model.Channel:
        t = model.TEMPLATE_BY_KEY[self.template]
        ch = model.Channel(number=self.number, name=self.name, template=t.key, mode=t.mode,
                           rotate_hours=t.rotate_hours, kind=self.kind, start_mode=t.start_mode)
        if self.kind == model.MOVIES:
            ch.movies = [i.key for i in self.items]
        else:
            ch.shows = [model.ShowRef(i.key, i.title) for i in self.items]
            ch.commercials = commercials
        return ch


def show_items(shows) -> list[Item]:
    from pathlib import Path

    out = []
    for s in shows:
        out.append(Item(s.folder, s.title, genre_set(s.genres), media_index.rating_tier(s.rating),
                        year_of(Path(s.folder).name), s.rating))
    return out


def movie_items(movies) -> list[Item]:
    return [Item(m.path, m.label, genre_set(m.genres), media_index.rating_tier(m.rating), m.year, m.rating, True)
            for m in movies]


# ------------------------------------------------------------------ similarity

_CLASH = {("family", "mature"), ("mature", "family")}
_STRAIN = {("family", "teen"), ("teen", "family"), ("pg", "mature"), ("mature", "pg")}


def distance(a: Item, b: Item, decade_weight: float = 0.12) -> float:
    if (a.tier, b.tier) in _CLASH:
        return INF
    if not a.genres and not b.genres:
        d = 0.6
    elif not a.genres or not b.genres:
        d = 0.9
    else:
        inter = sum(_WEIGHT.get(g, 1.0) for g in a.genres & b.genres)
        union = sum(_WEIGHT.get(g, 1.0) for g in a.genres | b.genres)
        d = 1.0 - inter / union
    if a.year and b.year:
        d += decade_weight * min(abs(a.year - b.year) / 30.0, 1.0)
    if (a.tier, b.tier) in _STRAIN:
        d += 0.25
    return d


def cluster(items: list[Item], n: int, decade_weight: float = 0.12) -> list[list[Item]]:
    """Average-linkage grouping into (about) n groups, never merging clashing ratings,
    biased towards evenly sized groups."""
    if not items:
        return []
    n = max(1, min(n, len(items)))
    groups: list[list[int]] = [[i] for i in range(len(items))]
    # pairwise item distances
    D = [[0.0] * len(items) for _ in items]
    for i in range(len(items)):
        for j in range(i + 1, len(items)):
            D[i][j] = D[j][i] = distance(items[i], items[j], decade_weight)
    # group-to-group sum of distances (INF if any pair clashes)
    S = {(i, j): D[i][j] for i in range(len(items)) for j in range(i + 1, len(items))}
    target = max(1.0, len(items) / n)
    alive = list(range(len(groups)))
    while len(alive) > n:
        best, pair = INF, None
        for x in range(len(alive)):
            gi = alive[x]
            for y in range(x + 1, len(alive)):
                gj = alive[y]
                s = S[(gi, gj)] if gi < gj else S[(gj, gi)]
                if s == INF:
                    continue
                na, nb = len(groups[gi]), len(groups[gj])
                score = s / (na * nb) + 0.18 * (na + nb) / target
                if score < best:
                    best, pair = score, (gi, gj)
        if pair is None:
            break                     # only clashing merges left: keep more groups
        gi, gj = pair
        groups[gi] += groups[gj]
        groups[gj] = []
        alive.remove(gj)
        for gk in alive:
            if gk == gi:
                continue
            a = S[(min(gi, gk), max(gi, gk))]
            b = S[(min(gj, gk), max(gj, gk))]
            S[(min(gi, gk), max(gi, gk))] = INF if INF in (a, b) else a + b
    out = [[items[i] for i in groups[g]] for g in alive]
    out.sort(key=lambda g: -len(g))
    for g in out:
        g.sort(key=lambda it: it.title.lower())
    return out


# ------------------------------------------------------------------ naming and styles

def _top_genres(group: list[Item]) -> list[str]:
    c: Counter = Counter()
    for it in group:
        for g in it.genres:
            c[g] += _WEIGHT.get(g, 1.0)
    return [g for g, _ in c.most_common()]


def _decade_prefix(group: list[Item]) -> str:
    years = [it.year for it in group if it.year]
    if len(years) < max(2, len(group) * 0.6):
        return ""
    decades = Counter((y // 10) * 10 for y in years)
    dec, k = decades.most_common(1)[0]
    if k >= 0.7 * len(group) and dec >= 1950:
        return f"{str(dec)[2:]}s " if dec < 2000 else f"{dec}s "
    return ""
    return ""


def name_for(group: list[Item], movies: bool, used: set[str] = frozenset()) -> str:
    """Name from the group's strongest genre whose name isn't taken yet (so three cartoon
    channels become e.g. Toon Zone, Kid Zone, Action Alley rather than Toon Zone 1-3)."""
    table = MOVIE_NAMES if movies else TV_NAMES
    top = _top_genres(group)
    kid_safe = any(it.tier == "family" for it in group) and all(
        it.tier in ("family", "pg", "none") for it in group)
    if not movies and kid_safe and ("kids" in top or "family" in top):
        top = ["kids"] + top               # a children's channel reads best as that
    names = [table[g] for g in top if g in table]
    base = next((n for n in names if n not in used), names[0] if names else ("Movie Mix" if movies else "Variety"))
    return _decade_prefix(group) + base


def template_for(group: list[Item]) -> str:
    episodic = sum(1 for it in group if it.genres & _EPISODIC)
    return "shuffle" if episodic >= len(group) / 2 else "marathon"


def _main_genre(it: Item) -> str:
    return max(it.genres, key=lambda g: (_WEIGHT.get(g, 1.0), g)) if it.genres else ""


def _genre_groups(items: list[Item], min_size: int) -> int:
    c = Counter(_main_genre(it) for it in items)
    return sum(1 for g, k in c.items() if g and k >= min_size)


def suggested_counts(n_shows: int, n_movies: int, movies=None, shows=None) -> tuple[int, int]:
    """About 8 shows per TV channel, but at least one per main genre with a few shows; for films,
    one channel per main genre with enough films (more needed in a bigger collection)."""
    tv = 0 if not n_shows else max(1, min(20, round(n_shows / 8)))
    if shows:
        tv = min(20, n_shows, max(tv, _genre_groups(show_items(shows), max(2, min(4, n_shows // 40)))))
    mv = 0 if not n_movies else max(1, min(8, round(n_movies / 40)))
    if movies:
        mv = max(1, min(8, _genre_groups(movie_items(movies), max(3, min(10, n_movies // 10)))))
    return tv, mv


def free_numbers(start: int, taken: set[int], count: int) -> list[int]:
    out, n = [], max(1, start)
    while len(out) < count:
        if n not in taken:
            out.append(n)
        n += 1
    return out


def propose(shows, movies, n_tv: int, n_movies: int, first_number: int,
            taken: set[int]) -> list[Proposal]:
    """The suggested lineup. shows/movies are library.LibShow / LibMovie lists."""
    tv_groups = cluster(show_items(shows), n_tv) if n_tv and shows else []
    mv_groups = cluster(movie_items(movies), n_movies, decade_weight=0.2) if n_movies and movies else []
    numbers = free_numbers(first_number, taken, len(tv_groups) + len(mv_groups))
    out: list[Proposal] = []
    names: Counter = Counter()

    def unique(name: str) -> str:
        names[name] += 1
        return name if names[name] == 1 else f"{name} {names[name]}"

    used: set[str] = set()
    for g in tv_groups:
        nm = name_for(g, False, used)
        used.add(nm.split(" ", 1)[-1] if nm[:1].isdigit() else nm)
        out.append(Proposal(unique(nm), numbers[len(out)], model.TV, template_for(g), g))
    for g in mv_groups:
        nm = name_for(g, True, used)
        used.add(nm.split(" ", 1)[-1] if nm[:1].isdigit() else nm)
        out.append(Proposal(unique(nm), numbers[len(out)], model.MOVIES, "movies", g))
    return out
