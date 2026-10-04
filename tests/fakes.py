from collections.abc import Sequence
from dataclasses import dataclass, field

from ammirror.errors import AuthError, ServiceError
from ammirror.models import ApplePlaylist, AppleTrack, YtmCandidate, YtmPlaylist, YtmPlaylistItem


@dataclass
class FakeYtm:
    search_results: dict[str, list[YtmCandidate]] = field(default_factory=dict)
    playlists: dict[str, YtmPlaylist] = field(default_factory=dict)
    liked: set[str] = field(default_factory=set)
    fail_on: set[str] = field(default_factory=set)
    auth_fail_on: set[str] = field(default_factory=set)
    calls: list[tuple] = field(default_factory=list)
    _next_id: int = 0

    def _record(self, name: str, *args: object) -> None:
        self.calls.append((name, *args))
        if name in self.fail_on:
            raise ServiceError(f"fake failure in {name}")
        if name in self.auth_fail_on:
            raise AuthError("ytm")

    def search_songs(self, query: str) -> list[YtmCandidate]:
        self._record("search_songs", query)
        return self.search_results.get(query, [])

    def get_playlist(self, playlist_id: str) -> YtmPlaylist | None:
        self._record("get_playlist", playlist_id)
        return self.playlists.get(playlist_id)

    def create_playlist(self, title: str) -> str:
        self._record("create_playlist", title)
        self._next_id += 1
        pid = f"PL{self._next_id}"
        self.playlists[pid] = YtmPlaylist(pid, title, ())
        return pid

    def rename_playlist(self, playlist_id: str, title: str) -> None:
        self._record("rename_playlist", playlist_id, title)
        p = self.playlists[playlist_id]
        self.playlists[playlist_id] = YtmPlaylist(p.id, title, p.items)

    def add_items(self, playlist_id: str, video_ids: Sequence[str]) -> None:
        self._record("add_items", playlist_id, tuple(video_ids))
        p = self.playlists[playlist_id]
        new = tuple(YtmPlaylistItem(v, f"set-{v}") for v in video_ids)
        self.playlists[playlist_id] = YtmPlaylist(p.id, p.title, p.items + new)

    def remove_items(self, playlist_id: str, items: Sequence[YtmPlaylistItem]) -> None:
        self._record("remove_items", playlist_id, tuple(items))
        p = self.playlists[playlist_id]
        gone = {i.set_video_id for i in items}
        kept = tuple(i for i in p.items if i.set_video_id not in gone)
        self.playlists[playlist_id] = YtmPlaylist(p.id, p.title, kept)

    def liked_video_ids(self) -> set[str]:
        self._record("liked_video_ids")
        return set(self.liked)

    def like(self, video_id: str) -> None:
        self._record("like", video_id)
        self.liked.add(video_id)

    def unlike(self, video_id: str) -> None:
        self._record("unlike", video_id)
        self.liked.discard(video_id)


@dataclass
class FakeApple:
    playlists: list[tuple[ApplePlaylist, list[AppleTrack]]] = field(default_factory=list)
    favorites: list[AppleTrack] = field(default_factory=list)
    failing_playlists: set[str] = field(default_factory=set)

    def library_playlists(self) -> list[ApplePlaylist]:
        return [p for p, _ in self.playlists]

    def playlist_tracks(self, playlist_id: str) -> list[AppleTrack]:
        if playlist_id in self.failing_playlists:
            raise ServiceError(f"fake failure for {playlist_id}")
        return next(ts for p, ts in self.playlists if p.id == playlist_id)

    def favorite_songs(self) -> list[AppleTrack]:
        return list(self.favorites)
