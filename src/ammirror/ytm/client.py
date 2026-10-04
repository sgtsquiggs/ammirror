import time
from collections.abc import Callable, Iterator, Sequence
from pathlib import Path
from typing import Any, Protocol

import requests
from ytmusicapi.exceptions import YTMusicError, YTMusicServerError

from ammirror.errors import AuthError, ServiceError
from ammirror.models import YtmCandidate, YtmPlaylist, YtmPlaylistItem

BATCH = 50
DESCRIPTION = "Mirrored from Apple Music by ammirror"


class YtmClient(Protocol):
    def search_songs(self, query: str) -> list[YtmCandidate]: ...
    def get_playlist(self, playlist_id: str) -> YtmPlaylist | None: ...
    def create_playlist(self, title: str) -> str: ...
    def rename_playlist(self, playlist_id: str, title: str) -> None: ...
    def add_items(self, playlist_id: str, video_ids: Sequence[str]) -> None: ...
    def remove_items(self, playlist_id: str, items: Sequence[YtmPlaylistItem]) -> None: ...
    def liked_video_ids(self) -> set[str]: ...
    def like(self, video_id: str) -> None: ...
    def unlike(self, video_id: str) -> None: ...


def _chunks[T](items: Sequence[T], size: int) -> Iterator[list[T]]:
    for i in range(0, len(items), size):
        yield list(items[i : i + size])


def _expect_succeeded(name: str, result: Any) -> None:
    status = result.get("status") if isinstance(result, dict) else result
    if status != "STATUS_SUCCEEDED":
        raise ServiceError(f"YouTube Music {name} failed: {result!r}")


def _candidate(r: dict[str, Any]) -> YtmCandidate | None:
    video_id = r.get("videoId")
    if not video_id or r.get("resultType", "song") != "song":
        return None
    artists = tuple(a["name"] for a in r.get("artists") or [] if a.get("name"))
    album = (r.get("album") or {}).get("name")
    return YtmCandidate(video_id, r.get("title") or "", artists, album, r.get("duration_seconds"))


class YtmusicapiClient:
    def __init__(
        self, yt: Any, *, sleep: Callable[[float], None] = time.sleep, write_delay: float = 0.5
    ) -> None:
        self._yt = yt
        self._sleep = sleep
        self._write_delay = write_delay

    @classmethod
    def from_auth_file(cls, path: Path) -> "YtmusicapiClient":
        if not path.exists():
            raise AuthError("ytm", "missing")
        from ytmusicapi import YTMusic

        return cls(YTMusic(str(path)))

    def _call(self, name: str, *args: Any, **kwargs: Any) -> Any:
        try:
            return getattr(self._yt, name)(*args, **kwargs)
        except YTMusicServerError as e:
            if any(code in str(e) for code in ("HTTP 401", "HTTP 403")):
                raise AuthError("ytm") from e
            raise ServiceError(f"YouTube Music {name} failed: {e}") from e
        except (YTMusicError, requests.RequestException) as e:
            raise ServiceError(f"YouTube Music {name} failed: {e}") from e

    def _write(self, name: str, *args: Any, **kwargs: Any) -> Any:
        result = self._call(name, *args, **kwargs)
        self._sleep(self._write_delay)
        return result

    def search_songs(self, query: str) -> list[YtmCandidate]:
        results = self._call("search", query, filter="songs", limit=10) or []
        return [c for r in results if (c := _candidate(r))]

    def get_playlist(self, playlist_id: str) -> YtmPlaylist | None:
        try:
            data = self._call("get_playlist", playlist_id, limit=None)
        except (KeyError, ServiceError) as e:
            # ytmusicapi raises a bare KeyError for missing playlists. Make sure the playlist
            # is really gone before reporting it missing, or a parser break would make sync
            # create a duplicate playlist every run.
            owned = self._call("get_library_playlists", limit=None) or []
            if any(p.get("playlistId") == playlist_id for p in owned):
                raise ServiceError(f"could not read YouTube Music playlist {playlist_id}") from e
            return None
        items = tuple(
            YtmPlaylistItem(t["videoId"], t["setVideoId"])
            for t in data.get("tracks") or []
            if t.get("videoId") and t.get("setVideoId")
        )
        return YtmPlaylist(data.get("id", playlist_id), data.get("title", ""), items)

    def create_playlist(self, title: str) -> str:
        result = self._write("create_playlist", title, DESCRIPTION, privacy_status="PRIVATE")
        if not isinstance(result, str):
            raise ServiceError(f"YouTube Music create_playlist failed: {result!r}")
        return result

    def rename_playlist(self, playlist_id: str, title: str) -> None:
        result = self._write("edit_playlist", playlist_id, title=title)
        _expect_succeeded("edit_playlist", result)

    def add_items(self, playlist_id: str, video_ids: Sequence[str]) -> None:
        unique = list(dict.fromkeys(video_ids))
        for batch in _chunks(unique, BATCH):
            result = self._write("add_playlist_items", playlist_id, videoIds=batch)
            _expect_succeeded("add_playlist_items", result)

    def remove_items(self, playlist_id: str, items: Sequence[YtmPlaylistItem]) -> None:
        for batch in _chunks(items, BATCH):
            videos = [{"videoId": i.video_id, "setVideoId": i.set_video_id} for i in batch]
            result = self._write("remove_playlist_items", playlist_id, videos)
            _expect_succeeded("remove_playlist_items", result)

    def liked_video_ids(self) -> set[str]:
        data = self._call("get_liked_songs", limit=None) or {}
        return {t["videoId"] for t in data.get("tracks") or [] if t.get("videoId")}

    def like(self, video_id: str) -> None:
        self._write("rate_song", video_id, "LIKE")

    def unlike(self, video_id: str) -> None:
        self._write("rate_song", video_id, "INDIFFERENT")
