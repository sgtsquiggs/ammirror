import time
from collections.abc import Callable, Iterator
from typing import Any, Protocol

import httpx

from ammirror.apple.token import load_developer_token
from ammirror.config import Config, Paths
from ammirror.errors import AuthError, ServiceError
from ammirror.models import ApplePlaylist, AppleTrack

API = "https://api.music.apple.com"
PAGE_LIMIT = 100
# Extra query params for the library-songs walk that surface `inFavorites` (see probe results).
FAVORITES_PARAMS: dict[str, str] = {}
_RETRY_STATUSES = {429, 500, 502, 503, 504}


class AppleLibrary(Protocol):
    def library_playlists(self) -> list[ApplePlaylist]: ...
    def playlist_tracks(self, playlist_id: str) -> list[AppleTrack]: ...
    def favorite_songs(self) -> list[AppleTrack]: ...


def _parse_track(item: dict[str, Any]) -> AppleTrack | None:
    attrs = item.get("attributes") or {}
    catalog = ((item.get("relationships") or {}).get("catalog") or {}).get("data") or []
    cat = catalog[0] if catalog else None
    cattrs = (cat or {}).get("attributes") or {}
    title = cattrs.get("name") or attrs.get("name")
    artist = cattrs.get("artistName") or attrs.get("artistName")
    if not title or not artist:
        return None
    return AppleTrack(
        library_id=item["id"],
        catalog_id=cat["id"] if cat else None,
        title=title,
        artist=artist,
        album=cattrs.get("albumName") or attrs.get("albumName") or "",
        duration_ms=cattrs.get("durationInMillis") or attrs.get("durationInMillis"),
        isrc=cattrs.get("isrc"),
    )


def _in_favorites(item: dict[str, Any]) -> bool:
    if (item.get("attributes") or {}).get("inFavorites"):
        return True
    catalog = ((item.get("relationships") or {}).get("catalog") or {}).get("data") or []
    return any((c.get("attributes") or {}).get("inFavorites") for c in catalog)


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
            try:
                resp = self._http.get(path, params=params)
            except httpx.TransportError as e:
                if last:
                    raise ServiceError(f"Apple Music request failed: {e}") from e
                self._sleep(float(2**attempt))
                continue
            if resp.status_code in (401, 403):
                raise AuthError("apple")
            if resp.status_code == 404 and missing_ok:
                return {"data": []}
            if resp.status_code in _RETRY_STATUSES and not last:
                self._sleep(self._retry_delay(resp, attempt))
                continue
            if resp.is_error:
                raise ServiceError(f"Apple Music API returned {resp.status_code} for {path}")
            return resp.json()
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
        while next_path:
            url = httpx.URL(next_path)
            body = self._get(url.path, {**params, **dict(url.params)}, missing_ok=missing_ok)
            yield from body.get("data", [])
            next_path = body.get("next")

    def library_playlists(self) -> list[ApplePlaylist]:
        return [
            ApplePlaylist(item["id"], (item.get("attributes") or {}).get("name", ""))
            for item in self._paginate("/v1/me/library/playlists", {"limit": str(PAGE_LIMIT)})
        ]

    def playlist_tracks(self, playlist_id: str) -> list[AppleTrack]:
        items = self._paginate(
            f"/v1/me/library/playlists/{playlist_id}/tracks",
            {"include": "catalog", "limit": str(PAGE_LIMIT)},
            missing_ok=True,  # Apple answers 404 for the tracks of an empty playlist
        )
        return [t for item in items if (t := _parse_track(item))]

    def favorite_songs(self) -> list[AppleTrack]:
        items = self._paginate(
            "/v1/me/library/songs",
            {"include": "catalog", "limit": str(PAGE_LIMIT), **FAVORITES_PARAMS},
        )
        return [t for item in items if _in_favorites(item) and (t := _parse_track(item))]
