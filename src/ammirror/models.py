import re
import unicodedata
from dataclasses import dataclass
from enum import StrEnum
from typing import Literal
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

_PUNCTUATION = str.maketrans(
    {
        **dict.fromkeys("\u2018\u2019\u201a\u201b\u2032", "'"),
        **dict.fromkeys("\u201c\u201d\u201e\u201f\u2033", '"'),
        **dict.fromkeys("\u2013\u2014\u2212", "-"),
    }
)


def normalize_playlist_name(name: str) -> str:
    """Fold a playlist name so quote style, dash style, spacing and case do not matter."""
    folded = unicodedata.normalize("NFKC", name).translate(_PUNCTUATION)
    return " ".join(folded.split()).casefold()


_LEADING_PUNCTUATION = re.compile(r"^[\W_]+")
_DIGITS = re.compile(r"[0-9]+")


def natural_sort_key(name: str) -> tuple[str, str, str]:
    """Sort key: numbers by value, case and quote style ignored, leading punctuation skipped."""
    folded = normalize_playlist_name(name)
    core = _LEADING_PUNCTUATION.sub("", folded)
    return _DIGITS.sub(lambda m: m.group().zfill(20), core), folded, name


def artwork_key(url: str) -> str:
    """Identify artwork by its URL minus the signing parameters some hosts renew per request.

    Apple serves custom playlist artwork from a blob store whose URLs carry a fresh
    X-Amz-* signature on every read; comparing raw URLs would re-upload it every sync.
    """
    parts = urlsplit(url)
    params = parse_qsl(parts.query, keep_blank_values=True)
    kept = [(k, v) for k, v in params if not k.lower().startswith("x-amz-")]
    if len(kept) == len(params):
        return url
    return urlunsplit(parts._replace(query=urlencode(kept)))


@dataclass(frozen=True)
class AppleTrack:
    library_id: str
    catalog_id: str | None
    title: str
    artist: str
    album: str
    duration_ms: int | None
    isrc: str | None

    @property
    def key(self) -> str:
        """Stable id for matching: catalog id when known, else the library id."""
        return self.catalog_id or self.library_id


class PlaylistOwner(StrEnum):
    """Who manages a library playlist, in the order playlists are listed."""

    YOURS = "yours"  # editable: made by the user
    APPLE = "apple"  # read-only, from Apple Music's catalog
    OTHER = "other"  # read-only system playlists such as Favorite Songs


@dataclass(frozen=True)
class ApplePlaylist:
    id: str
    name: str
    owner: PlaylistOwner = PlaylistOwner.YOURS
    artwork_url: str | None = None  # a square image, ready to download


@dataclass(frozen=True)
class YtmCandidate:
    video_id: str
    title: str
    artists: tuple[str, ...]
    album: str | None
    duration_s: int | None


@dataclass(frozen=True)
class YtmPlaylistItem:
    video_id: str
    set_video_id: str


@dataclass(frozen=True)
class YtmPlaylist:
    id: str
    title: str
    items: tuple[YtmPlaylistItem, ...]


class UnmatchedReason(StrEnum):
    NO_CATALOG = "no_catalog"
    NO_RESULTS = "no_results"
    LOW_SCORE = "low_score"
    AMBIGUOUS = "ambiguous"


@dataclass(frozen=True)
class Matched:
    video_id: str
    score: float
    method: Literal["search", "pin"]


@dataclass(frozen=True)
class Unmatched:
    reason: UnmatchedReason
    candidate: YtmCandidate | None = None
    candidate_score: float | None = None


Resolution = Matched | Unmatched


@dataclass(frozen=True)
class PlaylistMapping:
    apple_playlist_id: str
    ytm_playlist_id: str
    apple_name: str
    cover_key: str | None = None  # artwork_key of the cover last set on the copy


@dataclass(frozen=True)
class CreatePlaylist:
    apple_playlist_id: str
    apple_name: str
    title: str
    video_ids: tuple[str, ...]


@dataclass(frozen=True)
class RenamePlaylist:
    apple_playlist_id: str
    ytm_playlist_id: str
    apple_name: str
    title: str


@dataclass(frozen=True)
class AddItems:
    ytm_playlist_id: str
    title: str
    video_ids: tuple[str, ...]


@dataclass(frozen=True)
class RemoveItems:
    ytm_playlist_id: str
    title: str
    items: tuple[YtmPlaylistItem, ...]


@dataclass(frozen=True)
class SetCover:
    apple_playlist_id: str
    title: str
    artwork_url: str


@dataclass(frozen=True)
class Like:
    video_id: str
    apple_id: str


@dataclass(frozen=True)
class Unlike:
    video_id: str


@dataclass(frozen=True)
class ForgetLike:
    video_id: str


Op = (
    CreatePlaylist | RenamePlaylist | AddItems | RemoveItems | SetCover | Like | Unlike | ForgetLike
)


@dataclass(frozen=True)
class Plan:
    ops: tuple[Op, ...]
    warnings: tuple[str, ...] = ()
