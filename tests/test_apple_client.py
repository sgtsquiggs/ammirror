from collections.abc import Callable

import httpx
import pytest

from ammirror.apple.client import AppleClient
from ammirror.errors import AuthError, ServiceError
from ammirror.models import ApplePlaylist, AppleTrack

Handler = Callable[[httpx.Request], httpx.Response]


def client(handler: Handler, sleeps: list[float] | None = None) -> AppleClient:
    return AppleClient(
        "dev-token",
        "user-token",
        transport=httpx.MockTransport(handler),
        sleep=(sleeps.append if sleeps is not None else lambda _s: None),
    )


def lib_song(n: int, *, catalog: bool = True, fav: bool | None = None) -> dict:
    attrs: dict = {
        "name": f"Lib Song {n}",
        "artistName": "Lib Artist",
        "albumName": "Lib Album",
        "durationInMillis": 1000 * n,
    }
    if fav is not None:
        attrs["inFavorites"] = fav
    item: dict = {"id": f"i.{n}", "type": "library-songs", "attributes": attrs}
    item["relationships"] = {
        "catalog": {
            "data": [
                {
                    "id": f"{n}00",
                    "type": "songs",
                    "attributes": {
                        "name": f"Song {n}",
                        "artistName": "Artist",
                        "albumName": "Album",
                        "durationInMillis": 2000 * n,
                        "isrc": f"USX{n}",
                    },
                }
            ]
            if catalog
            else []
        }
    }
    return item


def test_sends_auth_headers_and_lists_playlists() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        assert req.headers["authorization"] == "Bearer dev-token"
        assert req.headers["music-user-token"] == "user-token"
        assert req.url.path == "/v1/me/library/playlists"
        if req.url.params.get("offset") == "1":
            return httpx.Response(200, json={"data": [{"id": "p.2", "attributes": {"name": "B"}}]})
        assert req.url.params["limit"] == "100"
        return httpx.Response(
            200,
            json={
                "data": [{"id": "p.1", "attributes": {"name": "A"}}],
                "next": "/v1/me/library/playlists?offset=1",
            },
        )

    result = client(handler).library_playlists()
    assert result == [ApplePlaylist("p.1", "A"), ApplePlaylist("p.2", "B")]


def test_pagination_keeps_original_params() -> None:
    seen: list[dict[str, str]] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(dict(req.url.params))
        if "offset" in req.url.params:
            return httpx.Response(200, json={"data": [lib_song(2)]})
        return httpx.Response(
            200,
            json={
                "data": [lib_song(1)],
                "next": "/v1/me/library/playlists/p.1/tracks?offset=100",
            },
        )

    tracks = client(handler).playlist_tracks("p.1")
    assert [t.catalog_id for t in tracks] == ["100", "200"]
    assert seen[1]["include"] == "catalog"
    assert seen[1]["offset"] == "100"


def test_playlist_tracks_prefers_catalog_attributes() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        assert req.url.path == "/v1/me/library/playlists/p.1/tracks"
        assert req.url.params["include"] == "catalog"
        return httpx.Response(200, json={"data": [lib_song(3), lib_song(4, catalog=False)]})

    t3, t4 = client(handler).playlist_tracks("p.1")
    assert t3 == AppleTrack("i.3", "300", "Song 3", "Artist", "Album", 6000, "USX3")
    assert t4 == AppleTrack("i.4", None, "Lib Song 4", "Lib Artist", "Lib Album", 4000, None)


def test_empty_playlist_404_is_empty() -> None:
    assert client(lambda _r: httpx.Response(404)).playlist_tracks("p.1") == []


def test_favorite_songs_filters_in_favorites() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        assert req.url.path == "/v1/me/library/songs"
        return httpx.Response(
            200, json={"data": [lib_song(1, fav=True), lib_song(2, fav=False), lib_song(3)]}
        )

    assert [t.catalog_id for t in client(handler).favorite_songs()] == ["100"]


@pytest.mark.parametrize("status", [401, 403])
def test_auth_failure(status: int) -> None:
    with pytest.raises(AuthError):
        client(lambda _r: httpx.Response(status)).library_playlists()


def test_retries_429_with_retry_after() -> None:
    calls = {"n": 0}
    sleeps: list[float] = []

    def handler(_req: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] < 3:
            return httpx.Response(429, headers={"Retry-After": "7"})
        return httpx.Response(200, json={"data": []})

    assert client(handler, sleeps).library_playlists() == []
    assert sleeps == [7.0, 7.0]


def test_gives_up_after_retries() -> None:
    sleeps: list[float] = []
    with pytest.raises(ServiceError, match="503"):
        client(lambda _r: httpx.Response(503), sleeps).library_playlists()
    assert sleeps == [1.0, 2.0, 4.0, 8.0]


def test_library_playlists_404_is_an_error() -> None:
    with pytest.raises(ServiceError, match="404"):
        client(lambda _r: httpx.Response(404)).library_playlists()


def test_favorite_songs_404_is_an_error() -> None:
    with pytest.raises(ServiceError, match="404"):
        client(lambda _r: httpx.Response(404)).favorite_songs()


def test_non_json_response_is_service_error() -> None:
    def handler(_req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html>maintenance</html>")

    with pytest.raises(ServiceError, match="not JSON"):
        client(handler).library_playlists()


def test_non_object_json_response_is_service_error() -> None:
    with pytest.raises(ServiceError, match="not JSON"):
        client(lambda _r: httpx.Response(200, json=["x"])).library_playlists()


def test_items_without_id_are_skipped() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/v1/me/library/playlists":
            return httpx.Response(
                200,
                json={"data": [{"attributes": {"name": "X"}}, {"id": "p.1"}, "junk"]},
            )
        no_id = lib_song(1)
        del no_id["id"]
        bad_catalog = lib_song(2)
        del bad_catalog["relationships"]["catalog"]["data"][0]["id"]
        return httpx.Response(200, json={"data": [no_id, bad_catalog, lib_song(3), None]})

    c = client(handler)
    assert c.library_playlists() == [ApplePlaylist("p.1", "")]
    tracks = c.playlist_tracks("p.1")
    assert [(t.library_id, t.catalog_id) for t in tracks] == [("i.2", None), ("i.3", "300")]
