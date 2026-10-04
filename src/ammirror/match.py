import logging
import re
import unicodedata
from collections.abc import Sequence
from difflib import SequenceMatcher

from ammirror.models import (
    AppleTrack,
    Matched,
    Resolution,
    Unmatched,
    UnmatchedReason,
    YtmCandidate,
)

log = logging.getLogger(__name__)

THRESHOLD = 0.75
MARGIN = 0.05
VERSION_MARKERS = frozenset(
    {
        "live",
        "acoustic",
        "remix",
        "instrumental",
        "karaoke",
        "demo",
        "cover",
        "unplugged",
        "acapella",
    }
)

_FEAT = re.compile(r"\s*[\(\[](?:feat\.?|ft\.?|featuring|with)\s[^\)\]]*[\)\]]", re.IGNORECASE)
_REMASTER = re.compile(
    r"\s*(?:-\s+|[\(\[])\s*(?:\d{4}\s+)?(?:digital(?:ly)?\s+)?remaster(?:ed)?"
    r"(?:\s+(?:version|\d{4}))*\s*[\)\]]?",
    re.IGNORECASE,
)
_ARTIST_SPLIT = re.compile(r"\s*(?:,|&|\+|\bfeat\.?\s|\bft\.?\s|\bfeaturing\s)\s*", re.IGNORECASE)
_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def _fold(s: str) -> str:
    decomposed = unicodedata.normalize("NFKD", s)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch)).lower()


def _clean(s: str) -> str:
    return _NON_ALNUM.sub(" ", _fold(s).replace("&", " and ")).strip()


def normalize_title(s: str) -> str:
    s = _FEAT.sub("", s)
    s = _REMASTER.sub("", s)
    return _clean(s)


def artist_names(s: str) -> set[str]:
    names = {_clean(part) for part in _ARTIST_SPLIT.split(s)}
    names.add(_clean(s))
    return {n for n in names if n}


def _artist_score(apple_artist: str, ytm_artists: Sequence[str]) -> float:
    apple = artist_names(apple_artist)
    ytm = {n for a in ytm_artists for n in artist_names(a)} | artist_names(", ".join(ytm_artists))
    if not ytm:
        return 0.0
    if apple & ytm:
        return 1.0
    return max(SequenceMatcher(None, a, b).ratio() for a in apple for b in ytm)


def score(track: AppleTrack, cand: YtmCandidate) -> float:
    norm_track_title = normalize_title(track.title)
    norm_cand_title = normalize_title(cand.title)
    title = SequenceMatcher(None, norm_track_title, norm_cand_title).ratio()
    artist_score_val = _artist_score(track.artist, cand.artists)
    total = 0.5 * title + 0.3 * artist_score_val
    if track.duration_ms is None or cand.duration_s is None:
        total += 0.1
    else:
        delta = abs(track.duration_ms / 1000 - cand.duration_s)
        if delta <= 3:
            total += 0.2
        elif delta <= 10:
            total += 0.12
        else:
            total -= 0.3
    if cand.album and track.album and normalize_title(cand.album) == normalize_title(track.album):
        total += 0.05
    track_markers = set(norm_track_title.split()) & VERSION_MARKERS
    cand_markers = set(norm_cand_title.split()) & VERSION_MARKERS
    if track_markers != cand_markers:
        total -= 0.4
    if artist_score_val < 0.5:
        total -= 0.3
    return round(total, 4)


def choose(track: AppleTrack, candidates: Sequence[YtmCandidate]) -> Resolution:
    if not candidates:
        return Unmatched(UnmatchedReason.NO_RESULTS)
    ranked = sorted(((score(track, c), c) for c in candidates), key=lambda p: p[0], reverse=True)
    if log.isEnabledFor(logging.DEBUG):
        for rank, (s, c) in enumerate(ranked[:3], 1):
            log.debug(
                "candidate %d for %s - %s: %.2f %r by %s (%ss)",
                rank,
                track.artist,
                track.title,
                s,
                c.title,
                ", ".join(c.artists),
                "?" if c.duration_s is None else c.duration_s,
            )
    best_score, best = ranked[0]
    if best_score < THRESHOLD:
        return Unmatched(UnmatchedReason.LOW_SCORE, best, best_score)
    best_title = normalize_title(best.title)
    for other_score, other in ranked[1:]:
        if best_score - other_score >= MARGIN:
            break
        if normalize_title(other.title) != best_title:
            return Unmatched(UnmatchedReason.AMBIGUOUS, best, best_score)
    return Matched(best.video_id, best_score, "search")


def search_query(track: AppleTrack) -> str:
    return f"{track.artist} {_FEAT.sub('', track.title)}".strip()
