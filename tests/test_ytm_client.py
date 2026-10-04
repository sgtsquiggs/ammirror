from typing import Any

import pytest
import requests
from ytmusicapi.exceptions import YTMusicServerError, YTMusicUserError

from ammirror.errors import AuthError, ServiceError
from ammirror.models import YtmCandidate, YtmPlaylist, YtmPlaylistItem
from ammirror.ytm.client import YtmusicapiClient


class StubYT:
    """Records calls; return values/side effects configured per method name."""

    def __init__(self, **returns: Any) -> None:
        self.returns = returns
        self.calls: list[tuple[str, tuple, dict]] = []

    def __getattr__(self, name: str):
        def method(*args: Any, **kwargs: Any) -> Any:
            self.calls.append((name, args, kwargs))
            value = self.returns.get(name)
            if isinstance(value, BaseException):
                raise value
            return value

        return method


def make(**returns: Any) -> tuple[YtmusicapiClient, StubYT]:
    yt = StubYT(**returns)
    return YtmusicapiClient(yt, sleep=lambda _s: None), yt


def test_search_songs_maps_results() -> None:
    client, yt = make(
        search=[
            {
                "resultType": "song",
                "videoId": "v1",
                "title": "Song",
                "artists": [{"name": "A", "id": "x"}, {"name": "B", "id": None}],
                "album": {"name": "Al", "id": "y"},
                "duration_seconds": 201,
            },
            {"resultType": "video", "videoId": "v2", "title": "MV"},
            {"resultType": "song", "videoId": None, "title": "Unavailable"},
        ]
    )
    assert client.search_songs("A Song") == [YtmCandidate("v1", "Song", ("A", "B"), "Al", 201)]
    assert yt.calls[0] == ("search", ("A Song",), {"filter": "songs", "limit": 10})


def test_get_playlist_maps_items() -> None:
    client, yt = make(
        get_playlist={
            "id": "PL1",
            "title": "Gym",
            "tracks": [
                {"videoId": "v1", "setVideoId": "s1"},
                {"videoId": None, "setVideoId": "s2"},
            ],
        }
    )
    assert client.get_playlist("PL1") == YtmPlaylist("PL1", "Gym", (YtmPlaylistItem("v1", "s1"),))
    assert yt.calls[0] == ("get_playlist", ("PL1",), {"limit": None})


def test_get_playlist_missing_returns_none() -> None:
    client, _ = make(
        get_playlist=KeyError("contents"), get_library_playlists=[{"playlistId": "PLother"}]
    )
    assert client.get_playlist("PL1") is None


def test_get_playlist_parse_error_on_existing_playlist_raises() -> None:
    client, _ = make(
        get_playlist=KeyError("contents"), get_library_playlists=[{"playlistId": "PL1"}]
    )
    with pytest.raises(ServiceError, match="could not read"):
        client.get_playlist("PL1")


def test_server_401_is_auth_error() -> None:
    client, _ = make(get_liked_songs=YTMusicServerError("Server returned HTTP 401: Unauthorized."))
    with pytest.raises(AuthError):
        client.liked_video_ids()


def test_other_errors_are_service_errors() -> None:
    client, _ = make(rate_song=YTMusicUserError("bad"))
    with pytest.raises(ServiceError):
        client.like("v1")


def test_create_playlist() -> None:
    client, yt = make(create_playlist="PLnew")
    assert client.create_playlist("Gym") == "PLnew"
    name, args, kwargs = yt.calls[0]
    assert (name, args) == ("create_playlist", ("Gym", "Mirrored from Apple Music by ammirror"))
    assert kwargs == {"privacy_status": "PRIVATE"}


def test_create_playlist_error_response() -> None:
    client, _ = make(create_playlist={"error": "nope"})
    with pytest.raises(ServiceError):
        client.create_playlist("Gym")


def test_add_items_chunks_and_dedupes() -> None:
    client, yt = make(add_playlist_items={"status": "STATUS_SUCCEEDED"})
    ids = [f"v{i}" for i in range(120)] + ["v0"]
    client.add_items("PL1", ids)
    batches = [c[2]["videoIds"] for c in yt.calls if c[0] == "add_playlist_items"]
    assert [len(b) for b in batches] == [50, 50, 20]
    assert [v for b in batches for v in b] == [f"v{i}" for i in range(120)]


def test_add_items_failure_status() -> None:
    client, _ = make(add_playlist_items={"status": "STATUS_FAILED"})
    with pytest.raises(ServiceError):
        client.add_items("PL1", ["v1"])


def test_remove_items_sends_set_video_ids() -> None:
    client, yt = make(remove_playlist_items="STATUS_SUCCEEDED")
    client.remove_items("PL1", [YtmPlaylistItem("v1", "s1")])
    assert yt.calls[0] == (
        "remove_playlist_items",
        ("PL1", [{"videoId": "v1", "setVideoId": "s1"}]),
        {},
    )


def test_like_unlike_and_liked_ids() -> None:
    client, yt = make(get_liked_songs={"tracks": [{"videoId": "v1"}, {"videoId": None}]})
    client.like("v1")
    client.unlike("v2")
    assert client.liked_video_ids() == {"v1"}
    assert yt.calls[0] == ("rate_song", ("v1", "LIKE"), {})
    assert yt.calls[1] == ("rate_song", ("v2", "INDIFFERENT"), {})
    assert yt.calls[2] == ("get_liked_songs", (), {"limit": None})


def test_rename() -> None:
    client, yt = make(edit_playlist="STATUS_SUCCEEDED")
    client.rename_playlist("PL1", "New")
    assert yt.calls[0] == ("edit_playlist", ("PL1",), {"title": "New"})


def test_remove_items_failure_status() -> None:
    client, _ = make(remove_playlist_items={"status": "STATUS_FAILED"})
    with pytest.raises(ServiceError):
        client.remove_items("PL1", [YtmPlaylistItem("v1", "s1")])


def test_remove_items_chunks_at_50() -> None:
    client, yt = make(remove_playlist_items="STATUS_SUCCEEDED")
    items = [YtmPlaylistItem(f"v{i}", f"s{i}") for i in range(120)]
    client.remove_items("PL1", items)
    assert [len(c[1][1]) for c in yt.calls] == [50, 50, 20]


def test_rename_failure_status() -> None:
    client, _ = make(edit_playlist={"status": "STATUS_FAILED"})
    with pytest.raises(ServiceError):
        client.rename_playlist("PL1", "New")


def test_transport_errors_are_service_errors() -> None:
    client, _ = make(get_liked_songs=requests.ConnectionError("boom"))
    with pytest.raises(ServiceError):
        client.liked_video_ids()
