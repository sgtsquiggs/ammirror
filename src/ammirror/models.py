from dataclasses import dataclass
from enum import StrEnum
from typing import Literal


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


@dataclass(frozen=True)
class ApplePlaylist:
    id: str
    name: str


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
class ForgetPlaylist:
    apple_playlist_id: str
    apple_name: str


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
    CreatePlaylist
    | RenamePlaylist
    | AddItems
    | RemoveItems
    | ForgetPlaylist
    | Like
    | Unlike
    | ForgetLike
)


@dataclass(frozen=True)
class Plan:
    ops: tuple[Op, ...]
    warnings: tuple[str, ...] = ()
