import logging
import re
import time
from collections.abc import Callable, Iterator, Sequence
from pathlib import Path
from typing import Any, Protocol

import requests
from ytmusicapi.exceptions import YTMusicError, YTMusicServerError, YTMusicUserError

from ammirror.errors import YTMUSICAPI_HINT, AuthError, ServiceError
from ammirror.models import YtmCandidate, YtmPlaylist, YtmPlaylistItem

log = logging.getLogger(__name__)

BATCH = 50
RATE_LIMIT_RETRIES = 4  # backoff 1, 2, 4, 8 s
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
    def like_status(self, video_id: str) -> str | None: ...


_REQUEST_LINE = re.compile(
    r"^(?:(?:GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS|CONNECT|TRACE) \S+.*|.* HTTP/\d(?:\.\d)?)$"
)
_PSEUDO_NAME = re.compile(r"^:[A-Za-z-]+")
_BODY_HEADERS = frozenset({"content-encoding", "content-length"})


def sanitize_ytm_headers_report(raw: str) -> tuple[str, list[str]]:
    """Drop pasted lines that describe the browser's own request, keep the rest verbatim.

    Firefox's "Copy Request Headers" includes the HTTP request line, HTTP/2-3
    pseudo-headers, and content-encoding/content-length of the browser's (gzipped)
    body; ytmusicapi sends plain JSON, so these make YouTube answer HTTP 400.

    Also returns the names of the dropped lines (never their values).
    """
    kept: list[str] = []
    dropped: list[str] = []
    for line in raw.splitlines(keepends=True):
        text = line.rstrip("\r\n")
        if text.startswith(":"):
            m = _PSEUDO_NAME.match(text)
            dropped.append(m.group() if m else "<pseudo-header>")
            continue
        if _REQUEST_LINE.match(text):
            dropped.append("<request line>")
            continue
        name = text.partition(":")[0].strip().lower()
        if name in _BODY_HEADERS:
            dropped.append(name)
            continue
        kept.append(line)
    return "".join(kept), dropped


def sanitize_ytm_headers(raw: str) -> str:
    """Like `sanitize_ytm_headers_report`, returning only the cleaned headers."""
    return sanitize_ytm_headers_report(raw)[0]


def _describe_args(args: tuple[Any, ...], kwargs: dict[str, Any]) -> str:
    """Render call arguments for the debug log; long lists are shown as counts."""

    def one(v: Any) -> str:
        if isinstance(v, list | tuple) and len(v) > 10:
            return f"<{len(v)} items>"
        return repr(v)

    parts = [one(a) for a in args] + [f"{k}={one(v)}" for k, v in kwargs.items()]
    return ", ".join(parts)


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
        for attempt in range(RATE_LIMIT_RETRIES + 1):
            log.debug("ytm call %s(%s)", name, _describe_args(args, kwargs))
            try:
                return self._call_once(name, *args, **kwargs)
            except YTMusicServerError as e:
                if attempt == RATE_LIMIT_RETRIES:  # only rate limits get here
                    raise ServiceError(f"YouTube Music {name} failed: {e}") from e
                delay = float(2**attempt)
                log.debug("ytm %s rate limited (HTTP 429); retrying in %gs", name, delay)
                self._sleep(delay)
        raise AssertionError("unreachable")

    def _call_once(self, name: str, *args: Any, **kwargs: Any) -> Any:
        """Call ytmusicapi once; rate-limit errors pass through for _call to retry."""
        try:
            return getattr(self._yt, name)(*args, **kwargs)
        except YTMusicServerError as e:
            if any(code in str(e) for code in ("HTTP 401", "HTTP 403")):
                raise AuthError("ytm") from e
            if "HTTP 429" in str(e):
                raise
            raise ServiceError(f"YouTube Music {name} failed: {e}") from e
        except YTMusicUserError as e:
            if "auth" in str(e).lower():
                raise AuthError("ytm") from e
            raise ServiceError(f"YouTube Music {name} failed: {e}") from e
        except (YTMusicError, requests.RequestException) as e:
            raise ServiceError(f"YouTube Music {name} failed: {e}") from e
        except (KeyError, IndexError, TypeError, ValueError) as e:
            # ytmusicapi scrapes YouTube Music's internal API; when the response shape
            # changes it fails with bare lookup/type errors until it is updated.
            detail = f"{type(e).__name__} {e}"
            raise ServiceError(f"YouTube Music {name} failed: {detail} {YTMUSICAPI_HINT}") from e

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
        except ServiceError as e:
            # ytmusicapi raises a bare KeyError (a ServiceError from _call) for missing
            # playlists. Make sure the playlist is really gone before reporting it missing,
            # or a parser break would make sync create a duplicate playlist every run.
            owned = self._call("get_library_playlists", limit=None) or []
            in_library = any(p.get("playlistId") == playlist_id for p in owned)
            log.debug(
                "playlist %s unreadable; library check: %s",
                playlist_id,
                "still in library" if in_library else "gone",
            )
            if in_library:
                raise ServiceError(
                    f"could not read YouTube Music playlist {playlist_id}: {e}"
                ) from e
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

    def like_status(self, video_id: str) -> str | None:
        data = self._call("get_watch_playlist", videoId=video_id, limit=1) or {}
        tracks = data.get("tracks") or []
        status = tracks[0].get("likeStatus") if tracks else None
        return status if isinstance(status, str) else None
