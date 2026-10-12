from collections.abc import Callable

import httpx
import pytest

from ammirror.apple.client import AppleClient
from ammirror.errors import AuthError, ServiceError
from ammirror.models import ApplePlaylist, AppleTrack, PlaylistOwner

Handler = Callable[[httpx.Request], httpx.Response]


def client(handler: Handler, sleeps: list[float] | None = None) -> AppleClient:
    return AppleClient(
        "dev-token",
        "user-token",
        transport=httpx.MockTransport(handler),
        sleep=(sleeps.append if sleeps is not None else lambda _s: None),
    )


def lib_song(n: int, *, catalog: bool = True) -> dict:
    attrs: dict = {
        "name": f"Lib Song {n}",
        "artistName": "Lib Artist",
        "albumName": "Lib Album",
        "durationInMillis": 1000 * n,
    }
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
    assert [(p.id, p.name) for p in result] == [("p.1", "A"), ("p.2", "B")]


@pytest.mark.parametrize(
    ("attrs", "owner"),
    [
        ({"canEdit": True, "hasCatalog": False}, PlaylistOwner.YOURS),
        ({"canEdit": True, "hasCatalog": True}, PlaylistOwner.YOURS),
        ({"canEdit": False, "hasCatalog": True}, PlaylistOwner.APPLE),
        ({"canEdit": False, "hasCatalog": False}, PlaylistOwner.OTHER),
        ({}, PlaylistOwner.OTHER),
    ],
)
def test_library_playlists_owner(attrs: dict, owner: PlaylistOwner) -> None:
    body = {"data": [{"id": "p.1", "attributes": {"name": "A", **attrs}}]}
    result = client(lambda _r: httpx.Response(200, json=body)).library_playlists()
    assert result == [ApplePlaylist("p.1", "A", owner)]


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


def favorites_handler(req: httpx.Request) -> httpx.Response:
    if req.url.path == "/v1/me/library/playlists":
        return httpx.Response(
            200,
            json={
                "data": [
                    {"id": "p.other", "attributes": {"name": "Other"}},
                    {"id": "p.fav", "attributes": {"name": "Favorite Songs"}},
                ]
            },
        )
    assert req.url.path == "/v1/me/library/playlists/p.fav/tracks"
    assert req.url.params["include"] == "catalog"
    return httpx.Response(200, json={"data": [lib_song(1), lib_song(2)]})


def test_favorite_songs_reads_the_favorites_playlist() -> None:
    tracks = client(favorites_handler).favorite_songs("favorite  songs")
    assert [t.catalog_id for t in tracks] == ["100", "200"]


def test_favorite_songs_without_matching_playlist_is_an_error() -> None:
    with pytest.raises(ServiceError, match=r"'Lieblingssongs'.*sync\.favorites_playlist"):
        client(favorites_handler).favorite_songs("Lieblingssongs")


@pytest.mark.parametrize("status", [401, 403])
def test_auth_failure(status: int) -> None:
    with pytest.raises(AuthError):
        client(lambda _r: httpx.Response(status)).library_playlists()


def test_check_auth_reads_storefront() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        return httpx.Response(200, json={"data": [{"id": "us", "type": "storefronts"}]})

    assert client(handler).check_auth() == "signed in (storefront us)"
    assert seen == ["/v1/me/storefront"]


@pytest.mark.parametrize("status", [401, 403])
def test_check_auth_failure(status: int) -> None:
    with pytest.raises(AuthError):
        client(lambda _r: httpx.Response(status)).check_auth()


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


def test_favorite_songs_playlist_list_404_is_an_error() -> None:
    with pytest.raises(ServiceError, match="404"):
        client(lambda _r: httpx.Response(404)).favorite_songs("Favorite Songs")


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
    assert c.library_playlists() == [ApplePlaylist("p.1", "", PlaylistOwner.OTHER)]
    tracks = c.playlist_tracks("p.1")
    assert [(t.library_id, t.catalog_id) for t in tracks] == [("i.2", None), ("i.3", "300")]


@pytest.mark.parametrize(
    ("artwork", "url"),
    [
        (
            {"url": "https://img.example/a/{w}x{h}bb.jpg", "width": 1200, "height": 1200},
            "https://img.example/a/1200x1200bb.jpg",
        ),
        (
            {"url": "https://img.example/a/{w}x{h}cc.jpg", "width": 1080, "height": 1080},
            "https://img.example/a/1080x1080cc.jpg",
        ),
        ({"url": "https://img.example/a/{w}x{h}bb.jpg"}, "https://img.example/a/1200x1200bb.jpg"),
        (
            {"url": "https://img.example/a/{w}x{h}bb.jpg", "width": 3000, "height": 3000},
            "https://img.example/a/1200x1200bb.jpg",
        ),
        (
            {"url": "https://img.example/a/1200x1200AM.jpg?t=x", "width": 1200, "height": 1200},
            "https://img.example/a/1200x1200AM.jpg?t=x",
        ),
        ({"url": ""}, None),
        ("not a dict", None),
    ],
)
def test_library_playlists_artwork(artwork: object, url: str | None) -> None:
    def handler(_req: httpx.Request) -> httpx.Response:
        item = {"id": "p.1", "attributes": {"name": "A", "canEdit": True, "artwork": artwork}}
        return httpx.Response(200, json={"data": [item]})

    assert client(handler).library_playlists()[0].artwork_url == url


def test_artwork_download_never_sends_apple_tokens() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        assert req.url == "https://img.example/a/1200x1200bb.jpg"
        assert "authorization" not in req.headers
        assert "music-user-token" not in req.headers
        return httpx.Response(200, content=b"\xff\xd8jpeg", headers={"content-type": "image/jpeg"})

    assert client(handler).artwork("https://img.example/a/1200x1200bb.jpg") == b"\xff\xd8jpeg"


@pytest.mark.parametrize(
    ("response", "msg"),
    [
        (httpx.Response(404), "returned 404"),
        (httpx.Response(200, text="<html>", headers={"content-type": "text/html"}), "not .*image"),
        (
            httpx.Response(
                200, content=b"x" * (10 * 1024 * 1024 + 1), headers={"content-type": "image/jpeg"}
            ),
            "too large",
        ),
    ],
)
def test_artwork_download_errors(response: httpx.Response, msg: str) -> None:
    with pytest.raises(ServiceError, match=msg):
        client(lambda _req: response).artwork("https://img.example/a.jpg")


def test_artwork_transport_error_is_a_service_error() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom", request=req)

    with pytest.raises(ServiceError, match="artwork download failed"):
        client(handler).artwork("https://img.example/a.jpg")
