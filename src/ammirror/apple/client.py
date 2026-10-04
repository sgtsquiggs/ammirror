import logging
import time
from collections.abc import Callable, Iterator
from typing import Any, Protocol

import httpx

from ammirror.apple.token import load_developer_token
from ammirror.config import Config, Paths
from ammirror.errors import AuthError, ServiceError
from ammirror.models import ApplePlaylist, AppleTrack, normalize_playlist_name

log = logging.getLogger(__name__)

API = "https://api.music.apple.com"
PAGE_LIMIT = 100
_RETRY_STATUSES = {429, 500, 502, 503, 504}


class AppleLibrary(Protocol):
    def library_playlists(self) -> list[ApplePlaylist]: ...
    def playlist_tracks(self, playlist_id: str) -> list[AppleTrack]: ...
    def favorite_songs(self, playlist_name: str) -> list[AppleTrack]: ...


def _parse_track(item: dict[str, Any]) -> AppleTrack | None:
    attrs = item.get("attributes") or {}
    catalog = ((item.get("relationships") or {}).get("catalog") or {}).get("data") or []
    cat = catalog[0] if catalog else None
    cattrs = (cat or {}).get("attributes") or {}
    title = cattrs.get("name") or attrs.get("name")
    artist = cattrs.get("artistName") or attrs.get("artistName")
    if not title or not artist or not item.get("id"):
        return None
    return AppleTrack(
        library_id=item["id"],
        catalog_id=(cat or {}).get("id") or None,
        title=title,
        artist=artist,
        album=cattrs.get("albumName") or attrs.get("albumName") or "",
        duration_ms=cattrs.get("durationInMillis") or attrs.get("durationInMillis"),
        isrc=cattrs.get("isrc"),
    )


class AppleClient:
    def __init__(
        self,
        developer_token: str,
        user_token: str,
        *,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
        max_retries: int = 4,
    ) -> None:
        self._http = httpx.Client(
            base_url=API,
            transport=transport,
            timeout=30.0,
            headers={"Authorization": f"Bearer {developer_token}", "Music-User-Token": user_token},
        )
        self._sleep = sleep
        self._max_retries = max_retries

    @classmethod
    def from_paths(cls, paths: Paths, cfg: Config) -> "AppleClient":
        token_file = paths.apple_user_token_file
        if not token_file.exists():
            raise AuthError("apple", "missing")
        return cls(load_developer_token(cfg.apple), token_file.read_text().strip())

    def close(self) -> None:
        self._http.close()

    def _get(
        self, path: str, params: dict[str, str], *, missing_ok: bool = False
    ) -> dict[str, Any]:
        for attempt in range(self._max_retries + 1):
            last = attempt == self._max_retries
            log.debug("GET %s %s", path, params)
            try:
                resp = self._http.get(path, params=params)
            except httpx.TransportError as e:
                if last:
                    raise ServiceError(f"Apple Music request failed: {e}") from e
                delay = float(2**attempt)
                log.debug("GET %s failed (%s); retrying in %gs", path, type(e).__name__, delay)
                self._sleep(delay)
                continue
            log.debug("GET %s -> %d", path, resp.status_code)
            if resp.status_code in (401, 403):
                raise AuthError("apple")
            if resp.status_code == 404 and missing_ok:
                return {"data": []}
            if resp.status_code in _RETRY_STATUSES and not last:
                delay = self._retry_delay(resp, attempt)
                log.debug("GET %s returned %d; retrying in %gs", path, resp.status_code, delay)
                self._sleep(delay)
                continue
            if resp.is_error:
                raise ServiceError(f"Apple Music API returned {resp.status_code} for {path}")
            try:
                body = resp.json()
            except ValueError:
                body = None
            if not isinstance(body, dict):
                raise ServiceError(f"Apple Music API response for {path} is not JSON")
            return body
        raise AssertionError("unreachable")

    @staticmethod
    def _retry_delay(resp: httpx.Response, attempt: int) -> float:
        try:
            return float(resp.headers["Retry-After"])
        except (KeyError, ValueError):
            return float(2**attempt)

    def _paginate(
        self, path: str, params: dict[str, str], *, missing_ok: bool = False
    ) -> Iterator[dict[str, Any]]:
        next_path: str | None = path
        page = 0
        while next_path:
            page += 1
            url = httpx.URL(next_path)
            body = self._get(url.path, {**params, **dict(url.params)}, missing_ok=missing_ok)
            # Skip malformed entries rather than failing the whole read on one bad item.
            items = [item for item in body.get("data") or [] if isinstance(item, dict)]
            log.debug("%s page %d: %d items", url.path, page, len(items))
            yield from items
            next_path = body.get("next")

    def library_playlists(self) -> list[ApplePlaylist]:
        return [
            ApplePlaylist(item["id"], (item.get("attributes") or {}).get("name", ""))
            for item in self._paginate("/v1/me/library/playlists", {"limit": str(PAGE_LIMIT)})
            if item.get("id")
        ]

    def playlist_tracks(self, playlist_id: str) -> list[AppleTrack]:
        items = self._paginate(
            f"/v1/me/library/playlists/{playlist_id}/tracks",
            {"include": "catalog", "limit": str(PAGE_LIMIT)},
            missing_ok=True,  # Apple answers 404 for the tracks of an empty playlist
        )
        return [t for item in items if (t := _parse_track(item))]

    def favorite_songs(self, playlist_name: str) -> list[AppleTrack]:
        """Read favorites from Apple's auto-generated favorites playlist.

        The library songs endpoint only reports `inFavorites` with `extend=inFavorites`,
        which is slow and misses favorited songs not in the library.
        """
        wanted = normalize_playlist_name(playlist_name)
        for playlist in self.library_playlists():
            if normalize_playlist_name(playlist.name) == wanted:
                return self.playlist_tracks(playlist.id)
        raise ServiceError(
            f"no Apple Music playlist named '{playlist_name}' for favorites;"
            " set sync.favorites_playlist"
        )
